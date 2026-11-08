"use strict";

// Solidity immutable fields are resolved by the EVM at deploy time, so the
// artifact's deployedBytecode contains zero placeholders while eth_getCode
// returns the same runtime with the constructor values filled in. A naive
// byte-for-byte comparison therefore always rejects valid contracts that
// expose immutable state. This helper verifies that two runtimes only differ
// in those zero-placeholder windows.

const HEX_RE = /^0x[0-9a-f]*$/;

function toBytes(hex, label) {
  const text = String(hex || "").toLowerCase();
  if (!HEX_RE.test(text) || text.length % 2 !== 0) {
    throw new Error(`${label} must be an even-length 0x hex string`);
  }
  const body = text.slice(2);
  const bytes = new Array(body.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = parseInt(body.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function toHex(bytes) {
  let text = "0x";
  for (const value of bytes) {
    text += value.toString(16).padStart(2, "0");
  }
  return text;
}

function isZeroWindow(bytes, start) {
  for (let i = start; i < start + 32; i += 1) {
    if (bytes[i] !== 0) return false;
  }
  return true;
}

function isAddressWindow(bytes, start) {
  for (let i = start; i < start + 12; i += 1) {
    if (bytes[i] !== 0) return false;
  }
  for (let i = start + 12; i < start + 32; i += 1) {
    if (bytes[i] !== 0) return true;
  }
  return false;
}

/**
 * Returns true when onchainRuntime is the same EVM code as artifactRuntime
 * except for immutable address placeholders. Returns false when any other
 * byte differs or when a differing window cannot be attributed to an
 * immutable address placeholder.
 */
function matchesArtifactRuntime(artifactHex, onchainHex) {
  const artifactText = String(artifactHex || "").toLowerCase();
  const onchainText = String(onchainHex || "").toLowerCase();
  if (artifactText === onchainText) return true;
  if (!HEX_RE.test(artifactText) || !HEX_RE.test(onchainText)) return false;
  if (artifactText.length !== onchainText.length) return false;

  const artifact = toBytes(artifactText, "artifact runtime");
  const onchain = toBytes(onchainText, "onchain runtime");
  const diffs = [];
  for (let i = 0; i < artifact.length; i += 1) {
    if (artifact[i] !== onchain[i]) diffs.push(i);
  }
  if (diffs.length === 0) return true;

  const processed = new Set();
  const windows = [];
  for (const diffOffset of diffs) {
    if (processed.has(diffOffset)) continue;
    let found = null;
    for (let start = Math.max(0, diffOffset - 31); start <= diffOffset; start += 1) {
      const end = start + 31;
      if (end >= artifact.length) continue;
      if (!isZeroWindow(artifact, start)) continue;
      if (!isAddressWindow(onchain, start)) continue;
      let coversUnprocessedDiff = false;
      for (const candidate of diffs) {
        if (candidate >= start && candidate <= end && !processed.has(candidate)) {
          coversUnprocessedDiff = true;
          break;
        }
      }
      if (!coversUnprocessedDiff) continue;
      found = { start, end };
      break;
    }
    if (!found) return false;
    for (const candidate of diffs) {
      if (candidate >= found.start && candidate <= found.end) {
        processed.add(candidate);
      }
    }
    windows.push(found);
  }

  const normalized = onchain.slice();
  for (const window of windows) {
    for (let i = window.start; i <= window.end; i += 1) {
      normalized[i] = artifact[i];
    }
  }
  return toHex(normalized) === artifactText;
}

module.exports = { matchesArtifactRuntime };
