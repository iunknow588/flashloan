// Token registry helpers for the CoW flashloan probe CLI.

const fs = require("fs");
const path = require("path");
const { envFirst } = require("./config");

const APP_RUNTIME_ROOT = path.resolve(__dirname, "../../../../flashloan/app_runtime");

function loadJson(file) {
  if (!fs.existsSync(file)) return {};
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return {};
  }
}


module.exports = {
  tokenKey,
  stripUsdt,
  isAddress,
  registerToken,
  loadTokenOverrides,
  loadTokenRegistry,
  requireToken,
};
