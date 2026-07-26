import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request

from observer import ASSETS, ensure_database_schema, require_psycopg


APP_DIR = Path(__file__).resolve().parent
OBSERVER_PATH = APP_DIR / "observer.py"

app = Flask(__name__)
observer_process: Optional[subprocess.Popen] = None
selected_symbols: list[str] = []


def configured_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Attach Replit SQL first.")
    return database_url


def is_observer_running() -> bool:
    return observer_process is not None and observer_process.poll() is None


def validate_symbols(raw_symbols: object) -> list[str]:
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols must be a list")

    symbols = []
    for value in raw_symbols:
        symbol = str(value).strip().upper()
        if symbol not in ASSETS:
            raise ValueError(f"unsupported symbol: {symbol}")
        symbols.append(symbol)

    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise ValueError("select at least one symbol")
    return symbols


def observation_count() -> Optional[int]:
    try:
        database_url = configured_database_url()
        ensure_database_schema(database_url)
        psycopg = require_psycopg()
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM observations")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return None


@app.get("/")
def index():
    asset_items = "\n".join(
        f"""
        <label class="asset">
            <input type="checkbox" value="{symbol}" checked>
            <span>
                <strong>{symbol}</strong>
                <small>{asset.symbol}</small>
            </span>
        </label>
        """
        for symbol, asset in ASSETS.items()
    )

    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Observer Control</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      color: #17202a;
      background: #f6f7f9;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid #d9dee7;
      background: #ffffff;
    }}
    h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 700;
    }}
    main {{
      width: min(1040px, 100%);
      margin: 0 auto;
      padding: 24px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
    }}
    button {{
      min-height: 38px;
      border: 1px solid #b8c0cc;
      border-radius: 6px;
      padding: 0 14px;
      background: #ffffff;
      color: #17202a;
      font-weight: 700;
      cursor: pointer;
    }}
    button.primary {{
      border-color: #0f766e;
      background: #0f766e;
      color: #ffffff;
    }}
    button.danger {{
      border-color: #b42318;
      color: #b42318;
    }}
    button:disabled {{
      opacity: 0.5;
      cursor: not-allowed;
    }}
    .status {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .metric {{
      border: 1px solid #d9dee7;
      border-radius: 8px;
      padding: 14px;
      background: #ffffff;
    }}
    .metric small {{
      display: block;
      margin-bottom: 6px;
      color: #667085;
    }}
    .metric strong {{
      font-size: 18px;
    }}
    .assets {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 10px;
    }}
    .asset {{
      display: flex;
      gap: 10px;
      align-items: center;
      min-height: 56px;
      border: 1px solid #d9dee7;
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
    }}
    .asset small {{
      display: block;
      margin-top: 2px;
      color: #667085;
    }}
    dialog {{
      width: min(560px, calc(100% - 32px));
      border: 1px solid #cfd6e2;
      border-radius: 8px;
      padding: 0;
    }}
    dialog::backdrop {{ background: rgba(15, 23, 42, 0.38); }}
    .dialog-body {{ padding: 18px; }}
    .dialog-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      padding: 14px 18px;
      border-top: 1px solid #d9dee7;
      background: #f8fafc;
    }}
    .message {{
      min-height: 22px;
      color: #475467;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Binance x Aave Observer</h1>
    <div class="message" id="message"></div>
  </header>
  <main>
    <div class="toolbar">
      <button class="primary" id="startBtn">开始</button>
      <button id="stopBtn">停止</button>
      <button id="initBtn">初始化数据库</button>
      <button class="danger" id="clearBtn">清空数据库</button>
      <button class="danger" id="clearFilesBtn">清理旧文件</button>
      <button id="refreshBtn">刷新状态</button>
    </div>
    <section class="status">
      <div class="metric"><small>运行状态</small><strong id="running">-</strong></div>
      <div class="metric"><small>进程 PID</small><strong id="pid">-</strong></div>
      <div class="metric"><small>当前代币</small><strong id="symbols">-</strong></div>
      <div class="metric"><small>数据库行数</small><strong id="rows">-</strong></div>
    </section>
  </main>

  <dialog id="symbolDialog">
    <form method="dialog">
      <div class="dialog-body">
        <h2>选择要检测的代币</h2>
        <div class="assets">{asset_items}</div>
      </div>
      <div class="dialog-actions">
        <button value="cancel">取消</button>
        <button class="primary" id="confirmStart" value="default">确定并开始</button>
      </div>
    </form>
  </dialog>

  <script>
    const dialog = document.getElementById("symbolDialog");
    const message = document.getElementById("message");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const initBtn = document.getElementById("initBtn");
    const clearBtn = document.getElementById("clearBtn");
    const clearFilesBtn = document.getElementById("clearFilesBtn");
    const refreshBtn = document.getElementById("refreshBtn");

    function selectedSymbols() {{
      return Array.from(dialog.querySelectorAll("input[type=checkbox]:checked")).map(input => input.value);
    }}

    async function api(path, options = {{}}) {{
      const response = await fetch(path, {{
        headers: {{ "Content-Type": "application/json" }},
        ...options,
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "request failed");
      return data;
    }}

    async function refresh() {{
      try {{
        const status = await api("/api/status");
        document.getElementById("running").textContent = status.running ? "运行中" : "已停止";
        document.getElementById("pid").textContent = status.pid || "-";
        document.getElementById("symbols").textContent = status.symbols.length ? status.symbols.join(", ") : "-";
        document.getElementById("rows").textContent = status.rows ?? "-";
        startBtn.disabled = status.running;
        stopBtn.disabled = !status.running;
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }}

    startBtn.addEventListener("click", () => dialog.showModal());
    refreshBtn.addEventListener("click", refresh);
    initBtn.addEventListener("click", async () => {{
      try {{
        await api("/api/init", {{ method: "POST", body: "{{}}" }});
        message.textContent = "数据库已初始化";
        await refresh();
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }});
    stopBtn.addEventListener("click", async () => {{
      try {{
        await api("/api/stop", {{ method: "POST", body: "{{}}" }});
        message.textContent = "已停止";
        await refresh();
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }});
    clearBtn.addEventListener("click", async () => {{
      if (!confirm("确定清空所有观测数据？")) return;
      try {{
        await api("/api/clear", {{ method: "POST", body: "{{}}" }});
        message.textContent = "数据库已清空";
        await refresh();
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }});
    clearFilesBtn.addEventListener("click", async () => {{
      if (!confirm("确定删除旧 CSV 和旧图表文件？")) return;
      try {{
        const result = await api("/api/clear-files", {{ method: "POST", body: "{{}}" }});
        message.textContent = `已清理旧文件：${{result.deleted.length}} 个`;
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }});
    document.getElementById("confirmStart").addEventListener("click", async event => {{
      event.preventDefault();
      const symbols = selectedSymbols();
      try {{
        await api("/api/start", {{ method: "POST", body: JSON.stringify({{ symbols }}) }});
        dialog.close();
        message.textContent = "已开始检测";
        await refresh();
      }} catch (error) {{
        message.textContent = error.message;
      }}
    }});

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)


@app.get("/api/status")
def status():
    running = is_observer_running()
    return jsonify(
        {
            "running": running,
            "pid": observer_process.pid if running and observer_process else None,
            "symbols": selected_symbols if running else [],
            "rows": observation_count(),
        }
    )


@app.post("/api/start")
def start():
    global observer_process, selected_symbols

    if is_observer_running():
        return jsonify({"error": "observer is already running"}), 409

    try:
        symbols = validate_symbols(
            request.json.get("symbols") if request.json else None
        )
        database_url = configured_database_url()
        ensure_database_schema(database_url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    env = os.environ.copy()
    env["SYMBOLS"] = ",".join(symbols)
    observer_process = subprocess.Popen(
        [sys.executable, str(OBSERVER_PATH)], cwd=str(APP_DIR), env=env
    )
    selected_symbols = symbols
    return jsonify({"running": True, "pid": observer_process.pid, "symbols": symbols})


@app.post("/api/init")
def init_database():
    try:
        database_url = configured_database_url()
        ensure_database_schema(database_url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"initialized": True, "rows": observation_count()})


@app.post("/api/stop")
def stop():
    global observer_process, selected_symbols

    if not is_observer_running():
        observer_process = None
        selected_symbols = []
        return jsonify({"running": False})

    observer_process.terminate()
    try:
        observer_process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        observer_process.kill()
        observer_process.wait(timeout=3)

    observer_process = None
    selected_symbols = []
    return jsonify({"running": False})


@app.post("/api/clear")
def clear():
    try:
        database_url = configured_database_url()
        ensure_database_schema(database_url)
        psycopg = require_psycopg()
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE observations RESTART IDENTITY")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"cleared": True, "rows": 0})


@app.post("/api/clear-files")
def clear_files():
    deleted: list[str] = []
    errors: list[str] = []
    roots = {APP_DIR, Path.cwd()}

    for root in roots:
        csv_path = root / "observations.csv"
        if csv_path.exists():
            try:
                csv_path.unlink()
                deleted.append(str(csv_path))
            except OSError as exc:
                errors.append(f"{csv_path}: {exc}")

        charts_dir = root / "charts"
        if charts_dir.exists() and charts_dir.is_dir():
            for chart_path in charts_dir.glob("*.png"):
                try:
                    chart_path.unlink()
                    deleted.append(str(chart_path))
                except OSError as exc:
                    errors.append(f"{chart_path}: {exc}")
            try:
                charts_dir.rmdir()
                deleted.append(str(charts_dir))
            except OSError:
                pass

    status_code = 400 if errors else 200
    return jsonify({"deleted": deleted, "errors": errors}), status_code


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
