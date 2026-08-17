# Avalanche 多 DEX 扩展扫描

- 生成时间：`2026-08-15T02:45:42.127817+00:00`
- 代币数量：`14`
- V2 扩展池：`69`
- 推荐：`7`；可改善：`10`；拒绝：`52`
- 仅 V2 推荐池三角闭环：`0`
- V2 推荐 + 可改善三角观察闭环：`1`
- 合并当前 V3 + V2 推荐池图结构闭环：`1`
- 排除 USDt/USDT 等稳定币中间跳后的有效高质量闭环：`1`
- 合并当前 V3 + V2 推荐 + 可改善三角观察闭环：`10`

## 扫描结论

- V2 扩展扫描暂未发现推荐池闭环，但存在观察闭环；需要合并 V3/LB 后再判断。
- 合并当前 V3 与 V2 后已出现有效推荐闭环，下一步应对这些路径做精确报价复核。

## 合并 V3 + V2 有效高质量闭环

<table>
<thead>
<tr><th>路径</th><th>边 1</th><th>边 2</th><th>边 3</th><th>最低 TVL USD</th><th>下一步</th></tr>
</thead>
<tbody>
<tr><td>USDC -> WAVAX -> BTC.b -> USDC</td><td>BTC.b:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,065,746.11 / 0x2E587b9E...D0D28B</td><td>WAVAX:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,125,816.1 / 0xfAe3f424...b5c45E</td><td>BTC.b:WAVAX<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 106,970.42 / 0x2fD81391...91B806</td><td>106,970.42</td><td>进入 Router/Quoter 精确复核，不直接执行</td></tr>
</tbody>
</table>

## 合并 V3 + V2 观察路径（不计入有效高质量闭环）

- 本节只用于人工观察。包含 USDt/USDT/USDe/DAI.e 等稳定币中间跳，或包含黄色可改善池的路径，都不计入有效高质量闭环。

<table>
<thead>
<tr><th>路径</th><th>边 1</th><th>边 2</th><th>边 3</th><th>最低 TVL USD</th><th>结论</th></tr>
</thead>
<tbody>
<tr><td>USDC -> USDt -> WAVAX -> USDC</td><td>USDt:USDC<br>current_v3 / 推荐 / fee 100<br>TVL 282,686.57 / 0x804226cA...3347A0</td><td>USDt:WAVAX<br>pangolin_v2 / 可改善 / fee 30 bps<br>TVL 63,693.09 / 0xe3bA3d5e...2770da</td><td>WAVAX:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,125,816.1 / 0xfAe3f424...b5c45E</td><td>63,693.09</td><td>包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核</td></tr>
<tr><td>DAI.e -> USDC -> WAVAX -> DAI.e</td><td>USDC:DAI.e<br>current_v3 / 可改善 / fee 3000<br>TVL 40,754.42 / 0xA7141C79...fB92B3</td><td>WAVAX:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,125,816.1 / 0xfAe3f424...b5c45E</td><td>DAI.e:WAVAX<br>trader_joe_v1 / 可改善 / fee 30 bps<br>TVL 30,389.81 / 0x87Dee1cC...aed86a</td><td>30,389.81</td><td>包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核</td></tr>
<tr><td>BTC.b -> WAVAX -> WBTC.e -> BTC.b</td><td>BTC.b:WAVAX<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 106,970.42 / 0x2fD81391...91B806</td><td>WAVAX:WBTC.e<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 130,775.72 / 0xd5a37dC5...c88Ffa</td><td>BTC.b:WBTC.e<br>current_v3 / 可改善 / fee 100<br>TVL 20,184.62 / 0x4D8FBe53...2Acaac</td><td>20,184.62</td><td>包含可改善池，只观察，不自动执行</td></tr>
<tr><td>USDC -> WAVAX -> WETH.e -> USDC</td><td>WAVAX:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,125,816.1 / 0xfAe3f424...b5c45E</td><td>WAVAX:WETH.e<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 376,242.26 / 0xFE15c269...007AF9</td><td>WETH.e:USDC<br>current_v3 / 可改善 / fee 500<br>TVL 11,326.68 / 0x43fb9c3f...2726EB</td><td>11,326.68</td><td>包含可改善池，只观察，不自动执行</td></tr>
<tr><td>BTC.b -> USDC -> WBTC.e -> BTC.b</td><td>BTC.b:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,065,746.11 / 0x2E587b9E...D0D28B</td><td>WBTC.e:USDC<br>current_v3 / 可改善 / fee 3000<br>TVL 4,832.33 / 0x40857Abd...47D854</td><td>BTC.b:WBTC.e<br>current_v3 / 可改善 / fee 100<br>TVL 20,184.62 / 0x4D8FBe53...2Acaac</td><td>4,832.33</td><td>包含可改善池，只观察，不自动执行</td></tr>
<tr><td>USDC -> WAVAX -> WBTC.e -> USDC</td><td>WAVAX:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 1,125,816.1 / 0xfAe3f424...b5c45E</td><td>WAVAX:WBTC.e<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 130,775.72 / 0xd5a37dC5...c88Ffa</td><td>WBTC.e:USDC<br>current_v3 / 可改善 / fee 3000<br>TVL 4,832.33 / 0x40857Abd...47D854</td><td>4,832.33</td><td>包含可改善池，只观察，不自动执行</td></tr>
<tr><td>USDC -> USDt -> WETH.e -> USDC</td><td>USDt:USDC<br>current_v3 / 推荐 / fee 100<br>TVL 282,686.57 / 0x804226cA...3347A0</td><td>WETH.e:USDt<br>current_v3 / 可改善 / fee 10000<br>TVL 3,043.34 / 0x7967fa8F...99b360</td><td>WETH.e:USDC<br>current_v3 / 可改善 / fee 500<br>TVL 11,326.68 / 0x43fb9c3f...2726EB</td><td>3,043.34</td><td>包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核</td></tr>
<tr><td>USDt -> WAVAX -> WETH.e -> USDt</td><td>USDt:WAVAX<br>pangolin_v2 / 可改善 / fee 30 bps<br>TVL 63,693.09 / 0xe3bA3d5e...2770da</td><td>WAVAX:WETH.e<br>trader_joe_v1 / 推荐 / fee 30 bps<br>TVL 376,242.26 / 0xFE15c269...007AF9</td><td>WETH.e:USDt<br>current_v3 / 可改善 / fee 10000<br>TVL 3,043.34 / 0x7967fa8F...99b360</td><td>3,043.34</td><td>稳定币 USDt 起止闭环；可先做 USDC->USDt、末尾 USDt->USDC 桥接后复核</td></tr>
<tr><td>USDC -> USDe -> USDt -> USDC</td><td>USDe:USDC<br>current_v3 / 推荐 / fee 500<br>TVL 499,901.6 / 0x833E0fF4...7dE08B</td><td>USDe:USDt<br>current_v3 / 可改善 / fee 100<br>TVL 1,945.46 / 0xE04b2344...eb8c45</td><td>USDt:USDC<br>current_v3 / 推荐 / fee 100<br>TVL 282,686.57 / 0x804226cA...3347A0</td><td>1,945.46</td><td>包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核</td></tr>
</tbody>
</table>

## V2 扩展池明细

<table>
<thead>
<tr><th>交易对</th><th>DEX</th><th>池子</th><th>状态</th><th>TVL USD</th><th>token0 USD</th><th>token1 USD</th><th>0→1 保留率</th><th>1→0 保留率</th></tr>
</thead>
<tbody>
<tr><td>WAVAX:WETH.e</td><td>trader_joe_v1</td><td>0xFE15c269...007AF9</td><td>推荐</td><td>376,242.26</td><td>189,898.15</td><td>186,344.11</td><td>97.3231%</td><td>101.0608%</td></tr>
<tr><td>USDC:WAVAX</td><td>trader_joe_v1</td><td>0xf4003F4e...758adb</td><td>推荐</td><td>343,867.2</td><td>170,278.68</td><td>173,588.52</td><td>101.0463%</td><td>97.2405%</td></tr>
<tr><td>AAVE.e:WAVAX</td><td>trader_joe_v1</td><td>0xc3e6D9f7...26A34F</td><td>推荐</td><td>247,886.41</td><td>126,000.65</td><td>121,885.76</td><td>95.6869%</td><td>102.2297%</td></tr>
<tr><td>WAVAX:WETH.e</td><td>pangolin_v2</td><td>0x7c05d54f...B89c52</td><td>推荐</td><td>151,916.45</td><td>76,714.86</td><td>75,201.59</td><td>96.4795%</td><td>100.3755%</td></tr>
<tr><td>WAVAX:WBTC.e</td><td>trader_joe_v1</td><td>0xd5a37dC5...c88Ffa</td><td>推荐</td><td>130,775.72</td><td>66,141.64</td><td>64,634.08</td><td>95.9808%</td><td>100.4756%</td></tr>
<tr><td>BTC.b:WAVAX</td><td>trader_joe_v1</td><td>0x2fD81391...91B806</td><td>推荐</td><td>106,970.42</td><td>54,152.97</td><td>52,817.46</td><td>95.4833%</td><td>100.3271%</td></tr>
<tr><td>USDC:WAVAX</td><td>pangolin_v2</td><td>0x0e0100Ab...3a9665</td><td>推荐</td><td>84,651.86</td><td>41,878.02</td><td>42,773.84</td><td>99.4647%</td><td>95.3886%</td></tr>
<tr><td>USDt:WAVAX</td><td>pangolin_v2</td><td>0xe3bA3d5e...2770da</td><td>可改善</td><td>63,693.09</td><td>32,167.22</td><td>31,525.88</td><td>94.7747%</td><td>98.6097%</td></tr>
<tr><td>LINK.e:WAVAX</td><td>trader_joe_v1</td><td>0x6F3a0C89...2265D3</td><td>可改善</td><td>40,758.55</td><td>19,764.16</td><td>20,994.39</td><td>100.8201%</td><td>89.6026%</td></tr>
<tr><td>WAVAX:WBTC.e</td><td>pangolin_v2</td><td>0x5764b8D8...4EF5D3</td><td>可改善</td><td>39,904.97</td><td>20,180.85</td><td>19,724.12</td><td>92.8562%</td><td>97.1005%</td></tr>
<tr><td>USDt:WAVAX</td><td>trader_joe_v1</td><td>0xbb4646a7...27ab73</td><td>可改善</td><td>38,438.01</td><td>19,401.84</td><td>19,036.17</td><td>93.0399%</td><td>96.558%</td></tr>
<tr><td>DAI.e:WAVAX</td><td>trader_joe_v1</td><td>0x87Dee1cC...aed86a</td><td>可改善</td><td>30,389.81</td><td>15,062.45</td><td>15,327.36</td><td>95.1551%</td><td>91.993%</td></tr>
<tr><td>DAI.e:WAVAX</td><td>pangolin_v2</td><td>0xbA09679A...289AB0</td><td>可改善</td><td>29,667.44</td><td>14,703.76</td><td>14,963.68</td><td>95.0196%</td><td>91.8485%</td></tr>
<tr><td>LINK.e:WAVAX</td><td>pangolin_v2</td><td>0x5875c368...6DAbD4</td><td>可改善</td><td>27,598.09</td><td>13,390.01</td><td>14,208.08</td><td>98.46%</td><td>87.7986%</td></tr>
<tr><td>USDC:WAVAX</td><td>sushi_v2</td><td>0x6539bF46...39ED61</td><td>可改善</td><td>5,362.46</td><td>2,655.58</td><td>2,706.88</td><td>73.8863%</td><td>71.4822%</td></tr>
<tr><td>USDC:WBTC.e</td><td>pangolin_v2</td><td>0x9D86243A...7B80cf</td><td>可改善</td><td>4,316.17</td><td>2,164.69</td><td>2,151.48</td><td>67.8443%</td><td>68.5472%</td></tr>
<tr><td>AAVE.e:WAVAX</td><td>pangolin_v2</td><td>0x5944f135...8cc4fE</td><td>可改善</td><td>4,049.42</td><td>2,051.91</td><td>1,997.51</td><td>65.3188%</td><td>68.317%</td></tr>
<tr><td>FRAX:WAVAX</td><td>trader_joe_v1</td><td>0x862905a8...f52C89</td><td>拒绝</td><td>480.78</td><td>368.7</td><td>112.08</td><td>8.1823%</td><td>33.1438%</td></tr>
<tr><td>WAVAX:WETH.e</td><td>sushi_v2</td><td>0x2FdE1c28...c9B515</td><td>拒绝</td><td>393.81</td><td>198.6</td><td>195.21</td><td>16.2786%</td><td>16.6081%</td></tr>
<tr><td>WBTC.e:WETH.e</td><td>sushi_v2</td><td>0x55dE93B0...4CE79d</td><td>拒绝</td><td>288.06</td><td>143.81</td><td>144.26</td><td>12.6072%</td><td>12.5631%</td></tr>
<tr><td>USDC:USDt</td><td>trader_joe_v1</td><td>0x8D5dB5D4...626423</td><td>拒绝</td><td>149.3</td><td>74.79</td><td>74.51</td><td>6.9311%</td><td>6.9588%</td></tr>
<tr><td>FRAX:WAVAX</td><td>pangolin_v2</td><td>0x0CE543c0...a6b559</td><td>拒绝</td><td>114.64</td><td>87.9</td><td>26.73</td><td>2.4567%</td><td>8.5609%</td></tr>
<tr><td>USDC:WETH.e</td><td>pangolin_v2</td><td>0x0fcee9e2...8923c8</td><td>拒绝</td><td>66.84</td><td>33.48</td><td>33.36</td><td>3.2274%</td><td>3.2396%</td></tr>
<tr><td>BTC.b:WAVAX</td><td>pangolin_v2</td><td>0x142abd25...f12021</td><td>拒绝</td><td>55.68</td><td>28.08</td><td>27.6</td><td>2.6844%</td><td>2.7328%</td></tr>
<tr><td>USDC:USDt</td><td>pangolin_v2</td><td>0xe7dAC042...6a6a9d</td><td>拒绝</td><td>30.36</td><td>15.19</td><td>15.17</td><td>1.494%</td><td>1.4965%</td></tr>
<tr><td>EURC:WAVAX</td><td>pangolin_v2</td><td>0x8362741D...42463c</td><td>拒绝</td><td>24.53</td><td>13.05</td><td>11.48</td><td>1.1327%</td><td>1.2903%</td></tr>
<tr><td>DAI.e:USDC</td><td>pangolin_v2</td><td>0x86129F99...d0477F</td><td>拒绝</td><td>21.2</td><td>10.61</td><td>10.59</td><td>1.0475%</td><td>1.05%</td></tr>
<tr><td>USDC:WETH.e</td><td>trader_joe_v1</td><td>0x9A166ae3...4e10a9</td><td>拒绝</td><td>20.71</td><td>10.38</td><td>10.33</td><td>1.0221%</td><td>1.0276%</td></tr>
<tr><td>DAI.e:USDt</td><td>pangolin_v2</td><td>0x1C4ca66f...b6051D</td><td>拒绝</td><td>20.53</td><td>10.27</td><td>10.26</td><td>1.0156%</td><td>1.0162%</td></tr>
<tr><td>DAI.e:WAVAX</td><td>sushi_v2</td><td>0x55CF10BF...9CB948</td><td>拒绝</td><td>11.39</td><td>5.66</td><td>5.73</td><td>0.5702%</td><td>0.5625%</td></tr>
<tr><td>LINK.e:WETH.e</td><td>sushi_v2</td><td>0xb266d875...1A9546</td><td>拒绝</td><td>10.81</td><td>5.6</td><td>5.21</td><td>0.5183%</td><td>0.557%</td></tr>
<tr><td>USDt:WAVAX</td><td>sushi_v2</td><td>0x7e5E4b67...7D3aB5</td><td>拒绝</td><td>10.7</td><td>5.39</td><td>5.31</td><td>0.5284%</td><td>0.5362%</td></tr>
<tr><td>LINK.e:WETH.e</td><td>pangolin_v2</td><td>0x1b96C113...2821ba</td><td>拒绝</td><td>9.54</td><td>4.96</td><td>4.58</td><td>0.4554%</td><td>0.4941%</td></tr>
<tr><td>WAVAX:WBTC.e</td><td>sushi_v2</td><td>0xe1051D42...ffC667</td><td>拒绝</td><td>9.47</td><td>4.72</td><td>4.75</td><td>0.4728%</td><td>0.4699%</td></tr>
<tr><td>DAI.e:WBTC.e</td><td>pangolin_v2</td><td>0xD8f78241...693C17</td><td>拒绝</td><td>6.39</td><td>3.2</td><td>3.19</td><td>0.3176%</td><td>0.3191%</td></tr>
<tr><td>AAVE.e:WAVAX</td><td>sushi_v2</td><td>0x5fBE35bF...4739a7</td><td>拒绝</td><td>5.18</td><td>2.62</td><td>2.56</td><td>0.2552%</td><td>0.2613%</td></tr>
<tr><td>DAI.e:WETH.e</td><td>pangolin_v2</td><td>0xaD7D7699...ABf6fD</td><td>拒绝</td><td>4.36</td><td>2.18</td><td>2.18</td><td>0.2173%</td><td>0.2178%</td></tr>
<tr><td>BTC.b:USDC</td><td>trader_joe_v1</td><td>0x61F8C548...C15Ac8</td><td>拒绝</td><td>4.21</td><td>2.1</td><td>2.11</td><td>0.2105%</td><td>0.21%</td></tr>
<tr><td>DAI.e:USDC</td><td>trader_joe_v1</td><td>0x5e7e2077...E7E4b0</td><td>拒绝</td><td>2.11</td><td>1.06</td><td>1.06</td><td>0.1055%</td><td>0.1054%</td></tr>
<tr><td>BTC.b:WBTC.e</td><td>trader_joe_v1</td><td>0x64EBd12C...01b7FE</td><td>拒绝</td><td>1.28</td><td>0.64</td><td>0.64</td><td>0.0639%</td><td>0.0638%</td></tr>
<tr><td>BTC.b:WETH.e</td><td>trader_joe_v1</td><td>0x2f6F72f4...098A67</td><td>拒绝</td><td>0.44</td><td>0.22</td><td>0.22</td><td>0.022%</td><td>0.0221%</td></tr>
<tr><td>USDC:WBTC.e</td><td>trader_joe_v1</td><td>0x66Ab843D...7Ff2Aa</td><td>拒绝</td><td>0.4</td><td>0.2</td><td>0.2</td><td>0.0201%</td><td>0.0198%</td></tr>
<tr><td>LINK.e:WBTC.e</td><td>pangolin_v2</td><td>0x96718F86...f3b737</td><td>拒绝</td><td>0.21</td><td>0.11</td><td>0.1</td><td>0.0104%</td><td>0.011%</td></tr>
<tr><td>BTC.b:USDC</td><td>pangolin_v2</td><td>0x55E01CeF...998De5</td><td>拒绝</td><td>0.14</td><td>0.07</td><td>0.07</td><td>0.0071%</td><td>0.0072%</td></tr>
<tr><td>BTC.b:USDt</td><td>trader_joe_v1</td><td>0xB3A0a2e8...c58138</td><td>拒绝</td><td>0.14</td><td>0.07</td><td>0.07</td><td>0.0068%</td><td>0.0068%</td></tr>
<tr><td>LINK.e:WETH.e</td><td>trader_joe_v1</td><td>0xC782D38e...12e832</td><td>拒绝</td><td>0.13</td><td>0.07</td><td>0.07</td><td>0.0065%</td><td>0.0066%</td></tr>
<tr><td>WBTC.e:WETH.e</td><td>pangolin_v2</td><td>0x05cbBcf6...030e62</td><td>拒绝</td><td>0.11</td><td>0.06</td><td>0.05</td><td>0.0055%</td><td>0.0056%</td></tr>
<tr><td>USDC:WBTC.e</td><td>sushi_v2</td><td>0x93F77141...48EE81</td><td>拒绝</td><td>0.1</td><td>0.05</td><td>0.05</td><td>0.005%</td><td>0.0051%</td></tr>
<tr><td>AAVE.e:WETH.e</td><td>sushi_v2</td><td>0x0CA0ff7E...A68Dfd</td><td>拒绝</td><td>0.08</td><td>0.04</td><td>0.04</td><td>0.004%</td><td>0.004%</td></tr>
<tr><td>USDC:USDt</td><td>sushi_v2</td><td>0xff0996e7...A79157</td><td>拒绝</td><td>0.07</td><td>0.04</td><td>0.04</td><td>0.0037%</td><td>0.0036%</td></tr>
<tr><td>USDt:WETH.e</td><td>trader_joe_v1</td><td>0x8b21956c...7BB0f6</td><td>拒绝</td><td>0.07</td><td>0.03</td><td>0.03</td><td>0.0034%</td><td>0.0034%</td></tr>
<tr><td>USDC:WETH.e</td><td>sushi_v2</td><td>0xd57A1891...dF29Ce</td><td>拒绝</td><td>0.06</td><td>0.03</td><td>0.03</td><td>0.0028%</td><td>0.0028%</td></tr>
<tr><td>AAVE.e:USDC</td><td>trader_joe_v1</td><td>0xB4904C1b...771E13</td><td>拒绝</td><td>0.03</td><td>0.02</td><td>0.02</td><td>0.0016%</td><td>0.0016%</td></tr>
<tr><td>WBTC.e:WETH.e</td><td>trader_joe_v1</td><td>0x3A409097...f7d74E</td><td>拒绝</td><td>0.03</td><td>0.01</td><td>0.01</td><td>0.0013%</td><td>0.0013%</td></tr>
<tr><td>AAVE.e:USDC</td><td>sushi_v2</td><td>0x4a1F468A...ad969f</td><td>拒绝</td><td>0.02</td><td>0.01</td><td>0.01</td><td>0.001%</td><td>0.0009%</td></tr>
<tr><td>DAI.e:USDt</td><td>trader_joe_v1</td><td>0xc32E725D...b2c319</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0.0002%</td><td>0.0002%</td></tr>
<tr><td>LINK.e:WAVAX</td><td>sushi_v2</td><td>0xF34B86dd...7D2dDC</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0.0002%</td><td>0.0002%</td></tr>
<tr><td>DAI.e:USDC</td><td>sushi_v2</td><td>0xaaE274fE...DbE8Fd</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0.0001%</td><td>0.0001%</td></tr>
<tr><td>BTC.b:LINK.e</td><td>trader_joe_v1</td><td>0x1e054Be9...CbB46a</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0.0001%</td></tr>
<tr><td>AAVE.e:USDC</td><td>pangolin_v2</td><td>0xAC0F3028...cAc616</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>AAVE.e:USDt</td><td>pangolin_v2</td><td>0x12b353Ba...0a6bd8</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>DAI.e:FRAX</td><td>trader_joe_v1</td><td>0x0973cdd4...675759</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>DAI.e:WETH.e</td><td>sushi_v2</td><td>0x7ca47c7c...Dc7884</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>AAVE.e:LINK.e</td><td>trader_joe_v1</td><td>0x94866cbc...c0bA2a</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>AAVE.e:DAI.e</td><td>sushi_v2</td><td>0x865e0fdb...3f1D07</td><td>拒绝</td><td>0</td><td>0</td><td>0</td><td>0%</td><td>0%</td></tr>
<tr><td>USDC:sAVAX</td><td>trader_joe_v1</td><td>0x2EE95559...8Bc843</td><td>拒绝</td><td>-</td><td>-</td><td>0.23</td><td>-%</td><td>-%</td></tr>
<tr><td>WAVAX:sAVAX</td><td>pangolin_v2</td><td>0x4E9A38F0...0ff70D</td><td>拒绝</td><td>-</td><td>-</td><td>4,256.65</td><td>-%</td><td>-%</td></tr>
<tr><td>WAVAX:sAVAX</td><td>trader_joe_v1</td><td>0x4b946c91...89094d</td><td>拒绝</td><td>-</td><td>-</td><td>85,563.01</td><td>-%</td><td>-%</td></tr>
<tr><td>WETH.e:sAVAX</td><td>trader_joe_v1</td><td>0x4f5961e2...333910</td><td>拒绝</td><td>-</td><td>-</td><td>0.01</td><td>-%</td><td>-%</td></tr>
</tbody>
</table>
