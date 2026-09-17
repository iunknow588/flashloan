const fs = require("fs");
const p = "E:/2026OPC大赛/flashLoan/contract/robinhood-testnet/DEX/test/ArbitrageComposer.test.js";
let src = fs.readFileSync(p, "utf8");
const closing = "\n});";
const idx = src.lastIndexOf(closing);
if (idx === -1) throw new Error("describe closing not found");
const tokenZ = "0x1111111111111111111111111111111111111111";
const newTests = `
  it("executes the candidate with the highest deviation rather than the first one listed", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(300);
    const high = await makeCandidate(500);

    const preflight = await composer.runBest.staticCall(
      "${tokenZ}",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBest(
      "${tokenZ}",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    // Only the highest-deviation candidate is sent to the executor.
    expect(await executor.calls()).to.equal(1n);
  });

  it("falls back to the next highest deviation candidate when the top one fails", async function () {
    const { composer, executor } = await deployFixture();
    const marker = "0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE";
    const top = await makeCandidate(800, marker);
    const mid = await makeCandidate(500);
    const low = await makeCandidate(300);

    await executor.setRevertMarkerCurrency0(marker);
    const preflight = await composer.runBest.staticCall(
      "${tokenZ}",
      [low, mid, top],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBest(
      "${tokenZ}",
      [low, mid, top],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    // The top candidate (marker) reverts in the executor; mid is tried next.
    expect(await executor.calls()).to.equal(2n);
  });

  it("runBestDetailed follows the same highest-deviation ordering", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(300);
    const high = await makeCandidate(500);

    const preflight = await composer.runBestDetailed.staticCall(
      "${tokenZ}",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBestDetailed(
      "${tokenZ}",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(await executor.calls()).to.equal(1n);
  });
` + closing;
src = src.slice(0, idx) + newTests;
fs.writeFileSync(p, src);
console.log("tests updated");
