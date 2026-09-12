"""Start background jobs in workers, including when --preload is enabled."""


def post_worker_init(worker):
    from app import start_background_services, _WORKER_STARTUP_BUILD
    worker.wsgi.config["TRANSLATION_SERVER_CONFIG"] = {
        "build": _WORKER_STARTUP_BUILD,
        "worker_class": worker.cfg.worker_class_str,
        "workers": worker.cfg.workers,
        "threads": worker.cfg.threads,
        "preload_app": worker.cfg.preload_app,
    }
    start_background_services()
