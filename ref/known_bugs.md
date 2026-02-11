# Known Bugs: ThreadPoolExecutor + asyncio

> Critical issues to be aware of when mixing threading and asyncio

## 1. asyncio.run() Inside Threads

**Bug:** Calling `asyncio.run()` from a thread created by `ThreadPoolExecutor` can cause issues.

**Symptoms:**
- `RuntimeError: This event loop is already running`
- Deadlocks
- Inconsistent behavior

**Root Cause:** Event loops are thread-specific and don't share state safely.

**Workaround:**
```python
# Instead of asyncio.run() in threads:
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    result = loop.run_until_complete(coro)
finally:
    loop.close()
```

**Best Practice:** Use `loop.run_in_executor()` to offload blocking work from async code, not the reverse.

**Reference:** [Stack Overflow Discussion](https://stackoverflow.com/questions/69728812/asyncio-and-concurrent-futures-threadpoolexecutor)

---

## 2. ThreadPoolExecutor Swallows Exceptions

**Bug:** Exceptions raised in executor tasks are silently lost unless explicitly retrieved.

**CPython Issue:** [#120508](https://github.com/python/cpython/issues/120508)

**Example of Bug:**
```python
def buggy_task():
    raise ValueError("This exception is LOST!")

executor = ThreadPoolExecutor()
executor.submit(buggy_task)  # No error visible!
# Program continues as if nothing happened
```

**Fix 1: Always call result():**
```python
future = executor.submit(buggy_task)
try:
    future.result()  # Now exception is raised
except ValueError as e:
    handle_error(e)
```

**Fix 2: Add done callback:**
```python
def handle_exception(future):
    exc = future.exception()
    if exc:
        logger.error(f"Task failed: {exc}")

future = executor.submit(buggy_task)
future.add_done_callback(handle_exception)
```

**Fix 3: Wrap tasks:**
```python
def safe_task(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Task {func.__name__} failed: {e}")
            raise
    return wrapper

executor.submit(safe_task(buggy_task))
```

---

## 3. asyncio.create_task() Requires Running Loop

**Bug:** `asyncio.create_task()` fails if there's no running event loop.

**Symptoms:**
```
RuntimeError: no running event loop
```

**When This Happens:**
- Calling from synchronous code
- Calling from a thread (threads don't share the main loop)

**Detection:**
```python
try:
    loop = asyncio.get_running_loop()
    # Safe to use create_task
except RuntimeError:
    # No running loop - need different approach
```

**Workarounds:**

```python
# Option 1: Use get_event_loop() and run_until_complete
loop = asyncio.get_event_loop()
loop.run_until_complete(my_coroutine())

# Option 2: Run in executor from async context
await loop.run_in_executor(None, blocking_function)

# Option 3: Fire-and-forget with executor
executor.submit(asyncio.run, my_coroutine())
```

---

## 4. Thread Safety of asyncio Objects

**Bug:** asyncio queues, events, and locks are NOT thread-safe.

**Symptoms:**
- Race conditions
- Corrupted state
- Random crashes under load

**Example of Bug:**
```python
# DON'T DO THIS
async_queue = asyncio.Queue()

def thread_worker():
    async_queue.put_nowait(item)  # NOT THREAD-SAFE!
```

**Fix: Use call_soon_threadsafe:**
```python
def thread_worker():
    loop.call_soon_threadsafe(async_queue.put_nowait, item)
```

**Or use thread-safe alternatives:**
```python
import queue

thread_safe_queue = queue.Queue()  # For threads
async_queue = asyncio.Queue()      # For coroutines

# Bridge between them
async def bridge():
    while True:
        item = await loop.run_in_executor(None, thread_safe_queue.get)
        await async_queue.put(item)
```

---

## 5. GIL Considerations

**Issue:** ThreadPoolExecutor doesn't provide parallelism for CPU-bound work due to GIL.

**When to Use What:**

| Workload | Solution |
|----------|----------|
| I/O-bound (network, disk) | `asyncio` or `ThreadPoolExecutor` |
| CPU-bound (computation) | `ProcessPoolExecutor` |
| Mixed | Careful combination with boundaries |

**Reference:** [Hybrid asyncio-threadpool patterns](https://deepwiki.com/fluentpython/example-code/3.3-asyncio-with-threadpoolexecutor)

---

## Debugging Tips

### Enable asyncio Debug Mode

```python
asyncio.run(main(), debug=True)

# Or via environment variable
# PYTHONASYNCIODEBUG=1 python app.py
```

This will:
- Warn about slow callbacks (>100ms)
- Log coroutines that were never awaited
- Check for resource leaks

### Log All Executor Exceptions

```python
import logging

logging.getLogger('concurrent.futures').setLevel(logging.WARNING)
```

### Monitor Executor Queue

```python
executor = ThreadPoolExecutor(max_workers=10)

# Check pending work
pending = executor._work_queue.qsize()
if pending > 100:
    logger.warning(f"Executor backlog: {pending} tasks")
```

---

## Summary Checklist

- [ ] Never call `asyncio.run()` from executor threads
- [ ] Always handle `future.result()` or add done callbacks
- [ ] Check for running loop before `create_task()`
- [ ] Use `call_soon_threadsafe()` for cross-thread async operations
- [ ] Use `ProcessPoolExecutor` for CPU-bound work
- [ ] Enable asyncio debug mode in development
