"""
Runtime plumbing shared by the optimized final submission
=========================================================

Four things every script in this folder needs and none of the originals had:

1. **Paths, set once.** The repo root's `models/` and `models/twin/` go on sys.path, so
   a worker process started by the pool imports exactly the modules the parent did.

2. **Fast table I/O on polars.** Data tables are Parquet; summaries stay CSV.
   `read_table` / `write_table` pick the format from the file suffix. `read_table`
   caches static inputs per process (keyed by path and mtime), so a worker that scores
   twenty policies reads `orders.parquet` once. A CSV is written the way pandas' `to_csv`
   did (NaN as an empty field, booleans as True/False, os.linesep line endings), so it
   parses to the same values.

3. **pandas-exact reductions.** The published numbers came out of pandas groupby, whose
   float sums are Kahan-compensated and whose std is Welford's one-pass update, and out
   of numpy's pairwise `add.reduce`. A different summation order moves the last bit of a
   demand estimate, the Monte Carlo gamma parameters with it, and so the policy the twin
   scores. The reductions below reproduce each order exactly; tests/test_exactness.py
   checks them against pandas and numpy.

4. **The job context.** One process pool shared by every step of run_all.py, a
   once-only registry for the baseline twin runs several steps reuse, and a per-step
   log that stands in for the per-script log files the original run_all captured.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
for _p in (HERE, REPO_ROOT / "models", REPO_ROOT / "models" / "twin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# Opt-in (run_all.py --lean-twin-outputs, or each research script's flag): research twin
# runs skip inventory.parquet / replenishment_receipts.parquet / production.parquet, ~35 MB per
# 156-week run and ~9 GB across a run_all. Runs whose inventory is read back later keep it.
LEAN_TWIN_OUTPUTS = False


# ------------------------------------------------------------------- threads --
def default_workers() -> int:
    """Worker processes for the shared pool: every logical CPU but two (one for the
    parent's step drivers, one for the OS). Override with MEIO_WORKERS."""
    env = os.environ.get("MEIO_WORKERS")
    if env:
        return max(1, int(env))
    return max(1, (os.cpu_count() or 2) - 2)


def cap_child_threads(polars_threads: int = 2) -> None:
    """Call before creating a process pool. Children inherit the environment at spawn
    and read these at import, so N workers don't each start a full BLAS / polars thread
    pool and oversubscribe the cores (the originals' _limit_blas_threads, plus polars)."""
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[v] = "1"
    os.environ["POLARS_MAX_THREADS"] = str(polars_threads)
    os.environ["MEIO_IN_WORKER_POOL"] = "1"


def intra_threads() -> int:
    """Threads a single task may use for GIL-free numpy/scipy chunks. Inside the pool
    each worker gets one; a standalone process gets most of the machine."""
    if os.environ.get("MEIO_IN_WORKER_POOL") == "1":
        return 1
    return max(1, min(8, (os.cpu_count() or 2) - 1))


# ------------------------------------------------------------------------ io --
_TABLE_CACHE: dict = {}
_TABLE_LOCK = threading.Lock()


def read_table(path, columns: list[str] | None = None, cache: bool = True) -> pl.DataFrame:
    """A table by suffix -- Parquet (the project's data tables) or CSV (summaries,
    segmentation outputs) -- cached per process by (path, mtime, columns). Returned
    frames are immutable, so sharing one across callers is safe."""
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns, tuple(columns) if columns else None)
    if cache:
        with _TABLE_LOCK:
            hit = _TABLE_CACHE.get(key)
        if hit is not None:
            return hit
    if path.suffix.lower() == ".csv":
        df = pl.read_csv(path, columns=columns, infer_schema_length=10000)
    else:
        df = pl.read_parquet(path, columns=columns)
    if cache:
        with _TABLE_LOCK:
            _TABLE_CACHE[key] = df
    return df


def write_table(df: pl.DataFrame, path) -> None:
    """Parquet for a .parquet path, else a CSV with pandas `to_csv(index=False)` parity."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)
        return
    write_csv(df, path)


def write_csv(df: pl.DataFrame, path) -> None:
    """pandas `to_csv(index=False)` parity: NaN -> empty, bool -> True/False, and pandas'
    line terminator (os.linesep, so CRLF on Windows, as the original's files are)."""
    exprs = []
    for name, dtype in df.schema.items():
        if dtype in (pl.Float32, pl.Float64):
            exprs.append(pl.col(name).fill_nan(None))
        elif dtype == pl.Boolean:
            exprs.append(pl.when(pl.col(name)).then(pl.lit("True"))
                         .when(pl.col(name).not_()).then(pl.lit("False"))
                         .otherwise(pl.lit(None, dtype=pl.String)).alias(name))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    (df.with_columns(exprs) if exprs else df).write_csv(path, line_terminator=os.linesep)


def write_policy(df: pl.DataFrame, out_path) -> str:
    """A policy file for the twin, always Parquet (a .csv name is given the .parquet
    suffix). Returns the path written."""
    path = Path(out_path).with_suffix(".parquet")
    write_table(df, path)
    return str(path)


def read_policy(path) -> pl.DataFrame:
    return read_table(path, cache=False)


def write_text_shared(path, text: str, attempts: int = 20) -> None:
    """Write a file several worker processes may write with the same content at once
    (a scenario folder's scenario_config.json). Skips when the content is already there;
    otherwise writes a private temp file and swaps it in, retrying while Windows holds
    the target open in another process."""
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"could not write {path}: held open by another process")


def py_round(values, ndigits: int) -> list:
    """Python's round() per element -- correctly rounded decimal, unlike np.round, which
    multiplies by 10**n first. The twin's receipts used round(float(x), 2)."""
    return [round(v, ndigits) for v in np.asarray(values, dtype=float).tolist()]


# ------------------------------------------------- pandas-exact reductions --
def _positions(codes: np.ndarray, n_groups: int):
    """(groups, rows) for each position p: the groups with more than p rows, and the row
    index of each one's p-th row, rows taken in their original order."""
    codes = np.asarray(codes, dtype=np.int64)
    order = np.argsort(codes, kind="stable")
    counts = np.bincount(codes, minlength=n_groups)
    starts = np.zeros(n_groups, dtype=np.int64)
    if n_groups > 1:
        np.cumsum(counts[:-1], out=starts[1:])
    for p in range(int(counts.max(initial=0))):
        g = np.flatnonzero(counts > p)
        yield g, order[starts[g] + p]


def kahan_sum(values, codes, n_groups: int):
    """pandas groupby sum (cython group_sum): Kahan-compensated, rows in order, NaN
    skipped. Returns (sums, non-NaN counts)."""
    values = np.asarray(values, dtype=np.float64)
    sumx = np.zeros(n_groups)
    comp = np.zeros(n_groups)
    nobs = np.zeros(n_groups, dtype=np.int64)
    for g, rows in _positions(codes, n_groups):
        val = values[rows]
        ok = ~np.isnan(val)
        if not ok.all():
            g, val = g[ok], val[ok]
        nobs[g] += 1
        s = sumx[g]
        y = val - comp[g]
        t = s + y
        c = (t - s) - y
        c[c != c] = 0.0                     # GH#50367: inf input leaves NaN compensation
        comp[g] = c
        sumx[g] = t
    return sumx, nobs


def kahan_mean(values, codes, n_groups: int) -> np.ndarray:
    """pandas groupby mean (cython group_mean): Kahan sum / count, NaN when empty."""
    s, n = kahan_sum(values, codes, n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / np.maximum(n, 1), np.nan)


def welford_std(values, codes, n_groups: int, ddof: int = 1) -> np.ndarray:
    """pandas groupby std (cython group_var, name='std'): Welford's update in row order,
    sqrt(M2 / (n - ddof)), NaN when n <= ddof."""
    values = np.asarray(values, dtype=np.float64)
    mean = np.zeros(n_groups)
    m2 = np.zeros(n_groups)
    nobs = np.zeros(n_groups, dtype=np.int64)
    for g, rows in _positions(codes, n_groups):
        val = values[rows]
        ok = ~np.isnan(val)
        if not ok.all():
            g, val = g[ok], val[ok]
        nobs[g] += 1
        old = mean[g]
        new = old + (val - old) / nobs[g]
        mean[g] = new
        m2[g] = m2[g] + (val - new) * (val - old)
    out = np.full(n_groups, np.nan)
    ok = nobs > ddof
    out[ok] = np.sqrt(m2[ok] / (nobs[ok] - ddof))
    return out


def _pairwise(a: np.ndarray) -> np.ndarray:
    """numpy's pairwise_sum (8-way unrolled below 128 values) on each row of a 2-D block
    whose rows all hold the same number of values."""
    n = a.shape[1]
    if n < 8:
        res = np.zeros(a.shape[0])
        for i in range(n):
            res = res + a[:, i]
        return res
    if n <= 128:
        r = [a[:, j].copy() for j in range(8)]
        i = 8
        stop = n - (n % 8)
        while i < stop:
            for j in range(8):
                r[j] = r[j] + a[:, i + j]
            i += 8
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
        while i < n:
            res = res + a[:, i]
            i += 1
        return res
    n2 = n // 2
    n2 -= n2 % 8
    return _pairwise(a[:, :n2]) + _pairwise(a[:, n2:])


def numpy_sum(values, codes, n_groups: int) -> np.ndarray:
    """What `group_values.sum()` returns for each group (a pandas agg lambda, or a
    filtered Series' .sum()): numpy add.reduce = 0.0 + pairwise_sum, rows in order."""
    values = np.asarray(values, dtype=np.float64)
    codes = np.asarray(codes, dtype=np.int64)
    out = np.zeros(n_groups)
    if not len(values):
        return out
    order = np.argsort(codes, kind="stable")
    counts = np.bincount(codes, minlength=n_groups)
    starts = np.zeros(n_groups, dtype=np.int64)
    if n_groups > 1:
        np.cumsum(counts[:-1], out=starts[1:])
    sv = values[order]
    for size in np.unique(counts[counts > 0]):
        g = np.flatnonzero(counts == size)
        block = sv[starts[g][:, None] + np.arange(size)[None, :]]
        out[g] = 0.0 + _pairwise(block)
    return out


def nanvar_pandas(values, ddof: int = 1) -> float:
    """pandas Series.var (nanops.nanvar, no bottleneck): two-pass, numpy sums."""
    v = np.asarray(values, dtype=np.float64)
    v = v[~np.isnan(v)]
    count = len(v)
    if count - ddof <= 0:
        return float("nan")
    avg = v.sum(dtype=np.float64) / count
    sqr = (avg - v) ** 2
    return float(sqr.sum(dtype=np.float64) / (count - ddof))


def nanstd_pandas(values, ddof: int = 1) -> float:
    return float(np.sqrt(nanvar_pandas(values, ddof)))


def nanmean_pandas(values) -> float:
    v = np.asarray(values, dtype=np.float64)
    v = v[~np.isnan(v)]
    return float(v.sum(dtype=np.float64) / len(v)) if len(v) else float("nan")


def pandas_argsort(values, ascending: bool = True) -> np.ndarray:
    """The row order pandas `sort_values` (nargsort, kind='quicksort') produces, ties
    included -- the originals' drop_duplicates(keep='first') after a sort depends on it."""
    v = np.asarray(values)
    idx = np.arange(len(v))
    nan = np.isnan(v) if v.dtype.kind == "f" else np.zeros(len(v), dtype=bool)
    non_nans, non_nan_idx = v[~nan], idx[~nan]
    if not ascending:
        non_nans, non_nan_idx = non_nans[::-1], non_nan_idx[::-1]
    indexer = non_nan_idx[non_nans.argsort(kind="quicksort")]
    if not ascending:
        indexer = indexer[::-1]
    return np.concatenate([indexer, idx[nan]])


# ------------------------------------------------------------ job context --
class _InlineExecutor:
    """workers=1: run each task in the calling thread. Same interface as the pool."""

    def submit(self, fn, *args, **kwargs) -> Future:
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as e:          # noqa: BLE001 -- delivered through the future
            f.set_exception(e)
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class Context:
    """One process pool for every step of a run, fed by priority, plus results several
    steps share.

    `submit(fn, ..., priority=p)` queues a task; the dispatcher hands the pool a new task
    only when a worker is free, lowest priority number first. That matters because the
    long pole of run_all is a chain of RNG-sequential policy builds (segment_alpha_explore,
    the alpha sweep): each link is submitted only when the previous one finishes, and in
    a plain FIFO pool it would queue behind every twin run submitted meanwhile.

    `once(key, fn, ...)` submits fn the first time a key is asked for and hands every
    later caller the same future -- the `actual` / `ms_actual_s{seed}` baselines are
    built once however many steps need them, the race the originals avoided by building
    them serially before their pools started.

    `submit(..., lane=name)` sends a task to a dedicated single-process executor instead:
    every link of one RNG chain then runs in the same warm process, reusing its loaded
    demand panel and caches, and never waits for a pool slot.

    `spawn(fn, ...)` runs fn on a parent-side thread: step drivers and the sequential
    chains inside them, which mostly wait on pool futures."""

    def __init__(self, workers: int | None = None):
        self.workers = workers or default_workers()
        self._executor = None
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._once: dict = {}
        self._queue: list = []
        self._seq = 0
        self._running = 0
        self._closed = False
        self._dispatcher = None
        self._threads = None
        self._lanes: dict = {}

    def _start(self):
        if self._executor is None:
            if self.workers <= 1:
                self._executor = _InlineExecutor()
            else:
                cap_child_threads()
                self._executor = ProcessPoolExecutor(max_workers=self.workers)
                self._dispatcher = threading.Thread(target=self._dispatch, name="pool-dispatch", daemon=True)
                self._dispatcher.start()

    def submit(self, fn, *args, priority: int = 5, lane: str | None = None, **kwargs) -> Future:
        with self._lock:
            self._start()
            if lane is not None and not isinstance(self._executor, _InlineExecutor):
                ex = self._lanes.get(lane)
                if ex is None:
                    ex = self._lanes[lane] = ProcessPoolExecutor(max_workers=1)
                return ex.submit(fn, *args, **kwargs)
            if isinstance(self._executor, _InlineExecutor):
                inline = True
            else:
                inline = False
                fut: Future = Future()
                import heapq
                heapq.heappush(self._queue, (priority, self._seq, fn, args, kwargs, fut))
                self._seq += 1
                self._cv.notify_all()
        if inline:
            return self._executor.submit(fn, *args, **kwargs)
        return fut

    def _dispatch(self):
        import heapq
        while True:
            with self._cv:
                while not self._closed and (not self._queue or self._running >= self.workers):
                    self._cv.wait()
                if self._closed:
                    for *_, fut in self._queue:
                        fut.cancel()
                    self._queue.clear()
                    return
                _, _, fn, args, kwargs, fut = heapq.heappop(self._queue)
                if not fut.set_running_or_notify_cancel():
                    continue
                self._running += 1
            try:
                inner = self._executor.submit(fn, *args, **kwargs)
            except BaseException as e:          # noqa: BLE001 -- pool broken: fail the task
                with self._cv:
                    self._running -= 1
                    self._cv.notify_all()
                fut.set_exception(e)
                continue
            inner.add_done_callback(lambda f, fut=fut: self._done(f, fut))

    def _done(self, inner: Future, fut: Future):
        with self._cv:
            self._running -= 1
            self._cv.notify_all()
        exc = inner.exception() if not inner.cancelled() else RuntimeError("cancelled")
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(inner.result())

    def once(self, key, fn, *args, priority: int = 1, **kwargs) -> Future:
        with self._lock:
            fut = self._once.get(key)
        if fut is not None:
            return fut
        fut = self.submit(fn, *args, priority=priority, **kwargs)
        with self._lock:
            return self._once.setdefault(key, fut)

    def spawn(self, fn, *args, **kwargs) -> Future:
        from concurrent.futures import ThreadPoolExecutor
        with self._lock:
            if self._threads is None:
                self._threads = ThreadPoolExecutor(max_workers=64, thread_name_prefix="driver")
        return self._threads.submit(fn, *args, **kwargs)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        if self._threads is not None:
            self._threads.shutdown(wait=False, cancel_futures=True)
        for ex in self._lanes.values():
            ex.shutdown(wait=True, cancel_futures=True)
        self._lanes.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None


class StepLog:
    """Thread-safe log for one step: to a file (run_all) and/or the console (standalone)."""

    def __init__(self, path=None, echo: bool = True):
        self.path = Path(path) if path else None
        self.echo = echo
        self._lock = threading.Lock()
        self._fh = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "w", encoding="utf-8")

    def __call__(self, *parts) -> None:
        msg = " ".join(str(p) for p in parts)
        with self._lock:
            if self._fh:
                self._fh.write(msg + "\n")
                self._fh.flush()
            if self.echo:
                print(msg, flush=True)

    def exception(self, exc: BaseException) -> None:
        self("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def close(self):
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


def df_to_md(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in df.iter_rows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def show(df: pl.DataFrame) -> str:
    """A whole frame as text, for the logs (pandas' to_string(index=False) equivalent)."""
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=400, fmt_str_lengths=60,
                   tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):
        return str(df)
