INFO:     127.0.0.1:59818 - "GET /admin/api/metrics?mint=66X3TYEGxR3BVNy2Mbe7hHaTEANUNs4c6GjeBDemBCF HTTP/1.1" 200 OK
2026-05-01 10:21:42,223 - app.services.dealer_detector - ERROR - [庄家判定] 8jdgDj9A... 判定异常: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)
Traceback (most recent call last):
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/dealer_detector.py", line 295, in _run_dealer_check
    c001_enabled = get_setting(SessionLocal(), "dealer_c001_enabled")
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/settings_service.py", line 43, in get_setting
    setting = db.query(Setting).filter(Setting.key == key).first()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/query.py", line 2748, in first
    return self.limit(1)._iter().first()  # type: ignore
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/query.py", line 2847, in _iter
    result: Union[ScalarResult[_T], Result[_T]] = self.session.execute(
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2308, in execute
    return self._execute_internal(
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2180, in _execute_internal
    conn = self._connection_for_bind(bind)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2047, in _connection_for_bind
    return trans._connection_for_bind(engine, execution_options)
  File "<string>", line 2, in _connection_for_bind
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/state_changes.py", line 139, in _go
    ret_value = fn(self, *arg, **kw)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 1143, in _connection_for_bind
    conn = bind.connect()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 3269, in connect
    return self._connection_cls(self)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 145, in __init__
    self._dbapi_connection = engine.raw_connection()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 3293, in raw_connection
    return self.pool.connect()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 452, in connect
    return _ConnectionFairy._checkout(self)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 1269, in _checkout
    fairy = _ConnectionRecord.checkout(pool)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 716, in checkout
    rec = pool._do_get()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/impl.py", line 158, in _do_get
    raise exc.TimeoutError(
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)
2026-05-01 10:21:42,235 - app.services.trade_stream - WARNING - [实时流] 连接断开，尝试重连...
INFO:     127.0.0.1:59818 - "GET /admin/api/metrics?mint=66X3TYEGxR3BVNy2Mbe7hHaTEANUNs4c6GjeBDemBCF HTTP/1.1" 200 OK
2026-05-01 10:22:12,270 - app.services.dealer_detector - ERROR - [庄家判定] 8J3ftNoQ... 判定异常: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)
Traceback (most recent call last):
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/dealer_detector.py", line 295, in _run_dealer_check
    c001_enabled = get_setting(SessionLocal(), "dealer_c001_enabled")
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/settings_service.py", line 43, in get_setting
    setting = db.query(Setting).filter(Setting.key == key).first()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/query.py", line 2748, in first
    return self.limit(1)._iter().first()  # type: ignore
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/query.py", line 2847, in _iter
    result: Union[ScalarResult[_T], Result[_T]] = self.session.execute(
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2308, in execute
    return self._execute_internal(
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2180, in _execute_internal
    conn = self._connection_for_bind(bind)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2047, in _connection_for_bind
    return trans._connection_for_bind(engine, execution_options)
  File "<string>", line 2, in _connection_for_bind
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/state_changes.py", line 139, in _go
    ret_value = fn(self, *arg, **kw)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 1143, in _connection_for_bind
    conn = bind.connect()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 3269, in connect
    return self._connection_cls(self)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 145, in __init__
    self._dbapi_connection = engine.raw_connection()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 3293, in raw_connection
    return self.pool.connect()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 452, in connect
    return _ConnectionFairy._checkout(self)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 1269, in _checkout
    fairy = _ConnectionRecord.checkout(pool)
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/base.py", line 716, in checkout
    rec = pool._do_get()
  File "/Users/curtdk/.openclaw/workspace/fastapiGmgn/venv/lib/python3.9/site-packages/sqlalchemy/pool/impl.py", line 158, in _do_get
    raise exc.TimeoutError(
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)