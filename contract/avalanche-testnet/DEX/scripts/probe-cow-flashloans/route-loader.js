// Historical and live route loaders for the CoW flashloan probe CLI.

const fs = require("fs");
const path = require("path");
const { envFirst, normalizeNetwork } = require("./config");

const APP_RUNTIME_ROOT = path.resolve(__dirname, "../../../../flashloan/app_runtime");
const DEFAULT_HISTORY_PATH = path.resolve(APP_RUNTIME_ROOT, "runtime/logs/cow_execution_attempts.jsonl");

function readJsonl(pathname) {
  if (!fs.existsSync(pathname)) return [];
  return fs
    .readFileSync(pathname, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (_) {
        return null;
      }
    })
    .filter(Boolean);
}

function routeFromAttempt(row) {
  const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
  const route = Array.isArray(row.route_path) && row.route_path.length ? row.route_path : quote.path || quote.route;
  if (!Array.isArray(route) || route.length < 2) return null;
  return {
    network: normalizeNetwork(row.network || (row.market_state || {}).cow_filter?.network || "avalanche"),
    route: route.map((item) => String(item).trim()).filter(Boolean),
    amountHuman: String(quote.input_amount || row.amount || process.env.COW_FLASHLOAN_PROBE_AMOUNT || "1"),
    pair: row.pair || quote.pair || "",
    pairRank: row.pair_rank || quote.pair_rank || null,
    priorityReason: row.priority_reason || quote.priority_reason || "",
    observedAt: row.observed_at || row.created_at || null,
    ownPlan: quote.binance_execution_plan || (quote.route || {}).binance_execution_plan || null,
  };
}

function loadHistoryRoutes({ network, limit, onlyTop1 }) {
  const historyPath = envFirst("COW_FLASHLOAN_PROBE_HISTORY_PATH") || DEFAULT_HISTORY_PATH;
  const wantedNetwork = normalizeNetwork(network);
  const seen = new Set();
  const routes = [];
  for (const row of readJsonl(historyPath).reverse()) {
    const item = routeFromAttempt(row);
    if (!item || item.network !== wantedNetwork) continue;
    if (onlyTop1 && item.pairRank != null && Number(item.pairRank) !== 1) continue;
    const key = `${item.network}|${item.pair}|${item.route.join(">")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    routes.push(item);
    if (routes.length >= limit) break;
  }
  return { historyPath, routes };
}

module.exports = {
  readJsonl,
  routeFromAttempt,
  loadHistoryRoutes,
};
