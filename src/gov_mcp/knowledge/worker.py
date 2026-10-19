"""Single-writer rounds with independent liveness heartbeats and lease renewal."""

import threading
import time
import uuid

from .store import KBError


class Worker:
    def __init__(self, engine, driver):
        self.engine, self.driver = engine, driver
        self.owner = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.thread, self.active_run = None, None
        self.loop_thread = None

    def round(self, run, force=False):
        store = self.engine.store
        try:
            self.engine.poll(run, force)
            selected = self.engine.select_jobs(run)
            if not selected:
                # A restart after processed jobs but before publication still resumes publication.
                self.engine.publish_documents(run)
                store.finish(run, "no_model_work")
                return
            if self.engine.provider.name == "jev" and not self.engine.provider.api_key.strip():
                for key in selected:
                    job = store.get("jobs", key)
                    job.update(status="waiting_credentials", error="MISSING_API_KEY", retry_after=store.clock()+60)
                    store.update_job(run, job)
                store.finish(run, "waiting_credentials")
                return
            self.driver.run(self.engine, run)
            jobs = [store.get("jobs", key) for key in selected]
            if any(j["status"] != "processed" for j in jobs): raise KBError("INCOMPLETE_AGENT_WORK")
            self.engine.publish_documents(run)
            store.finish(run, "completed")
        except Exception as error:
            code = str(error) if isinstance(error, KBError) else "EXECUTION_UNAVAILABLE"
            store.put("state", "publication_retry_after", store.clock()+60)
            try:
                for key in run.get("jobs", []):
                    job = store.get("jobs", key)
                    if job["status"] != "processed":
                        job.update(status="waiting_retry", error=code, retry_after=store.clock()+60)
                        store.update_job(run, job)
                store.audit("run_failed", {"run_id": run["id"], "code": code})
            except KBError:
                pass
            store.abort(run, code)

    def tick(self):
        store, config = self.engine.store, self.engine.config
        store.put("state", "heartbeat", {"at": store.clock(), "owner": self.owner,
            "status": "running" if self.thread and self.thread.is_alive() else "idle" if config["sources"] else "waiting_sources"})
        if self.thread and self.thread.is_alive():
            try: store.renew(self.active_run, config["lease_seconds"])
            except KBError:
                saved = store.get("runs", self.active_run["id"])
                try: self.driver.stop(saved or self.active_run)
                except Exception: store.audit("stop_failed", {"run_id": self.active_run["id"]})
                store.abort(self.active_run, "timeout_or_lease_lost")
            return
        self.active_run = None
        trigger = store.get("state", "trigger")
        if not config.get("enabled") or (store.get("state", "paused") and not trigger) or not any(s.get("enabled") for s in config["sources"]): return
        due = any((store.get("sources", s["id"]) or {}).get("next_check", 0) <= store.clock()
            or (store.get("sources", s["id"]) or {}).get("source_config") != s
            or (store.get("sources", s["id"]) or {}).get("processing_hash") != self.engine.processing_hash
            for s in config["sources"] if s.get("enabled"))
        pending = any(j["status"] != "processed" and j.get("retry_after", 0) <= store.clock() for j in store.rows("jobs")
                      if j["id"] in {k for source in store.rows("sources") for k in source.get("job_ids", [])})
        publication = self.engine.needs_publication() and (store.get("state", "publication_retry_after") or 0) <= store.clock()
        if not due and not pending and not publication and not trigger: return
        run = store.claim(self.owner, config["lease_seconds"], config["run_budget_seconds"])
        if run is None: return
        if trigger: store.put("state", "trigger", None)
        self.active_run = run
        self.thread = threading.Thread(target=self.round, args=(run, bool(trigger)), daemon=True)
        self.thread.start()

    def start(self):
        def loop():
            while not self.stop_event.is_set():
                try: self.tick()
                except Exception as error:
                    self.engine.store.audit("heartbeat_failed", {"error_type": type(error).__name__})
                self.stop_event.wait(self.engine.config["heartbeat_seconds"])
        self.loop_thread = threading.Thread(target=loop, daemon=True)
        self.loop_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.active_run:
            try: self.driver.stop(self.engine.store.get("runs", self.active_run["id"]) or self.active_run)
            finally: self.engine.store.abort(self.active_run, "interrupted")
        if self.loop_thread: self.loop_thread.join(timeout=3)
