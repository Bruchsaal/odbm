# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import contextlib
import os
import shutil
import threading
import webbrowser

import oracledb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import alertBroker
import collectorBroker
import configBroker
import cryptoBroker
import storage
import widgetBroker
from dbBroker import (_oracle_message, close_pools, get_container_context,
                      run_command_json, run_query_json)
from alertBroker import alerts
from collectorBroker import collector
from historyBroker import history
from libBroker import QueryLibrary
from logBroker import logger

HOST = os.environ.get("ODBM_HOST", "127.0.0.1")
PORT = int(os.environ.get("ODBM_PORT", "9000"))

# Files shipped inside the bundle and unpacked next to the executable on first
# run. Credentials are deliberately NOT bundled - they are created empty here
# and never travel inside a released binary.
BUNDLED_DATA = {
    "queries.json": {},
    "settings.json": {},
}


def resource_path(relative_path):
    """Absolute path to a read-only bundled resource (dev and PyInstaller)."""
    final_path = storage.resource_path(relative_path)
    logger.debug("Path", f"Resolved '{relative_path}' -> {final_path}",
                 "exists" if os.path.exists(final_path) else "MISSING")
    return final_path


def check_and_deploy_data():
    """
    Ensure the writable data files exist in the execution directory, seeding
    them from the bundle when available. Existing user data is never touched.
    """
    logger.debug("Boot", f"Execution directory: {storage.get_data_dir()}")

    for filename, empty_default in BUNDLED_DATA.items():
        target_path = storage.data_file(filename)
        if os.path.exists(target_path):
            logger.debug("Boot", f"Found existing {filename}, skipping deploy")
            continue

        source_path = resource_path(filename)
        if os.path.exists(source_path) and os.path.abspath(source_path) != os.path.abspath(target_path):
            try:
                # copyfile, not copy2: copy2 would carry over PyInstaller's
                # restrictive _MEIPASS mode and leave these files owner-only.
                shutil.copyfile(source_path, target_path)
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(target_path, 0o666 & ~umask)
                logger.info("Boot", f"Deployed default {filename}")
                continue
            except OSError as e:
                logger.error("Boot", f"Could not extract {filename}", str(e))

        if not os.path.exists(target_path):
            try:
                storage.write_json(filename, empty_default)
                logger.info("Boot", f"Created empty {filename}")
            except OSError as e:
                logger.error("Boot", f"Failed to create {filename}", str(e))

    # Credentials file: create private and empty, never seeded from a bundle.
    if not os.path.exists(storage.data_file(configBroker.CONFIG_FILE)):
        storage.write_json(configBroker.CONFIG_FILE,
                           {"connections": [], "active_index": -1}, private=True)
        logger.info("Boot", "Created empty connections.json")


def _read_text_resource(name, label):
    """Serve a bundled licence document as plain text."""
    path = resource_path(name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        logger.error("Licence", f"{label} missing from this build", path)
        raise HTTPException(status_code=500, detail=f"{label} is missing from this build.")


# --- data models ---
class QueryRequest(BaseModel):
    sql: str
    binds: dict = Field(default_factory=dict)


class ConnectionModel(BaseModel):
    id: str = ""
    name: str = ""
    user: str = ""
    password: str = ""
    dsn: str = ""
    sysdba: bool = False
    collect: bool = False


class ConfigRequest(BaseModel):
    connections: list[ConnectionModel]
    active_index: int


class LibraryItemRequest(BaseModel):
    id: str
    desc: str = ""
    sql: str = ""
    mode: str = "QUERY"
    refreshOptimum: int | None = None
    historyName: str | None = None
    alert: dict | None = None


class TestConnectionRequest(BaseModel):
    user: str
    password: str = ""
    dsn: str
    sysdba: bool = False
    id: str = ""


class WidgetSettingsRequest(BaseModel):
    settings: dict


class CollectorRequest(BaseModel):
    enabled: bool
    interval: int = collectorBroker.DEFAULT_INTERVAL


def create_app():
    check_and_deploy_data()
    configBroker.migrate_plaintext_passwords()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        state = collectorBroker.load_state()
        if state["enabled"]:
            collector.start(state["interval"])
        else:
            collector.interval = state["interval"]
        yield
        collector.stop()
        close_pools()

    app = FastAPI(title="ODBM", version="2.2", lifespan=lifespan)
    lib = QueryLibrary()
    app.state.lib = lib
    collector.attach_library(lib)

    # --- widget routes ---
    @app.get("/api/widgets")
    async def get_widget_settings():
        return widgetBroker.load_widget_settings()

    @app.post("/api/widgets")
    async def save_widget_settings(req: WidgetSettingsRequest):
        widgetBroker.save_widget_settings(req.settings)
        return {"status": "saved"}

    # --- metric apis ---
    @app.get("/api/metric/{query_id}")
    async def get_metric(query_id: str, record_history: bool = Query(True, alias="history")):
        query = lib.get_query(query_id)
        if not query:
            return {"data": [{"error": f"Query ID '{query_id}' not found"}]}

        data = run_query_json(query['sql'], query_id)

        if data and isinstance(data[0], dict) and "error" not in data[0]:
            conn_id = configBroker.get_active_connection_id()
            # Recording is the background collector's job; the browser only
            # asks for it explicitly (kept for the legacy ?history=true call).
            if record_history:
                history.record(query.get('historyName'), data, conn_id)
            alerts.evaluate(query, data, conn_id)

        return {"data": data}

    # --- execution apis ---
    @app.post("/api/execute")
    async def execute_custom(request: QueryRequest):
        return {"data": run_query_json(request.sql, binds=request.binds)}

    @app.post("/api/execute/command")
    async def execute_command_endpoint(request: QueryRequest):
        return {"data": run_command_json(request.sql, binds=request.binds)}

    # --- config apis ---
    @app.get("/api/config")
    async def get_config():
        # Passwords never leave the process; only a has_password flag does.
        return configBroker.get_public_config()

    @app.post("/api/config")
    async def update_config(config: ConfigRequest):
        configBroker.save_config(config.model_dump())
        close_pools()  # credentials may have changed, retire cached sessions
        alerts.clear()  # thresholds re-evaluate against the new target
        return {"status": "saved"}

    @app.post("/api/config/test")
    async def test_connection_endpoint(conn: TestConnectionRequest):
        password = conn.password
        if not password and conn.id:
            # Testing a saved connection the UI never received the secret for.
            for stored in configBroker.load_config()["connections"]:
                if stored["id"] == conn.id:
                    password = cryptoBroker.decrypt(stored.get("password", ""))
                    break
        try:
            mode = oracledb.SYSDBA if conn.sysdba else oracledb.AUTH_MODE_DEFAULT
            c = oracledb.connect(user=conn.user, password=password, dsn=conn.dsn, mode=mode)
            c.close()
            return {"status": "success", "message": "Connection Successful!"}
        except oracledb.Error as e:
            # Driver-level failures carry no .args[0].code, so reuse the guarded
            # formatter rather than indexing blind.
            return {"status": "error", "message": _oracle_message(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # --- collector and alerts ---
    @app.get("/api/collector")
    async def get_collector():
        return collector.status()

    @app.post("/api/collector")
    async def set_collector(req: CollectorRequest):
        interval = max(collectorBroker.MIN_INTERVAL, req.interval)
        collectorBroker.save_state(req.enabled, interval)
        if req.enabled:
            collector.stop()          # restart so an interval change takes hold
            collector.start(interval)
        else:
            collector.stop()
            collector.interval = interval
        return collector.status()

    @app.get("/api/alerts")
    async def get_alerts(scope: str = "active"):
        conn_id = None if scope == "all" else configBroker.get_active_connection_id()
        active = alerts.active(conn_id)
        return {"worst": alerts.worst(conn_id), "count": len(active), "alerts": active}

    # --- database context ---
    @app.get("/api/dbinfo")
    async def get_db_info():
        """
        Which container this connection lands in. dba_* views are container
        scoped, so in a CDB root they silently omit every PDB - the UI needs
        to say so rather than showing a smaller number with no explanation.
        """
        return get_container_context()

    # --- log apis ---
    @app.get("/api/logs")
    async def get_logs():
        return logger.get_logs()

    @app.post("/api/logs/clear")
    async def clear_logs_endpoint():
        logger.clear()
        return {"status": "cleared"}

    # --- library apis ---
    @app.get("/api/library")
    async def get_library():
        return lib.get_all_queries()

    @app.post("/api/library")
    async def save_library_item(item: LibraryItemRequest):
        if not item.id.strip():
            raise HTTPException(status_code=400, detail="Query id is required")
        saved = lib.upsert_query(item.id.strip(), item.model_dump(exclude={"id"}))
        return {"status": "saved", "item": saved}

    @app.delete("/api/library/{query_id}")
    async def delete_library_item(query_id: str):
        if lib.delete_query(query_id):
            return {"status": "deleted"}
        raise HTTPException(status_code=404, detail="Query not found")

    @app.post("/api/library/reset")
    async def reset_library():
        count = lib.reset_to_defaults()
        return {"status": "reset", "count": count}

    # --- history apis ---
    @app.get("/api/history/options")
    async def get_history_options():
        return history.get_available_metrics(configBroker.get_active_connection_id())

    @app.get("/api/history/data")
    async def get_history_data(source: str, metric: str = None):
        return history.get_series(source, metric, configBroker.get_active_connection_id())

    @app.post("/api/history/clear")
    async def clear_history():
        history.clear(configBroker.get_active_connection_id())
        return {"status": "cleared"}

    # AGPL section 13: users interacting with this program over a network must be
    # offered the corresponding source. The UI footer links to these.
    @app.get("/licenses/agpl", response_class=PlainTextResponse)
    async def read_license():
        return _read_text_resource("LICENSE", "AGPL-3.0 licence text")

    @app.get("/licenses/third-party", response_class=PlainTextResponse)
    async def read_third_party():
        return _read_text_resource("THIRD-PARTY-NOTICES.md", "Third-party notices")

    @app.get("/")
    async def read_index():
        index = resource_path('static/index.html')
        if not os.path.exists(index):
            raise HTTPException(status_code=500,
                                detail="UI assets are missing from this build.")
        return FileResponse(index)

    static_dir = resource_path("static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    else:
        # A packaging fault, not a reason to take the whole API down.
        logger.error("Boot", "static/ not found, the web UI will not load", static_dir)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    print(f"--- ODBM starting on http://{HOST}:{PORT} ---")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warn("Boot", f"Listening on {HOST} - the API has no authentication",
                    "Anyone who can reach this port can run SQL against your database.")

    def open_browser():
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Timer(2, open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT)
