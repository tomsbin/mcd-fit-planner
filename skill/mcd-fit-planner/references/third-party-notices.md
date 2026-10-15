# 第三方来源及许可

## 回顾页商品配图与设计参考

`assets/big-mac-official.png` 原样取自[麦当劳中国官网「巨无霸」产品页](https://www.mcdonalds.com.cn/product/Big-Mac)的透明底商品图，核查日期 2026-10-15。原图地址：<https://officiaweb-afd.mcdonalds.com.cn/public/upload/attachment/2022/08/202208081011205699.png>。仅作商品视觉配图，不代表用户实际选择的套餐组成，不用图片推算营养。图片版权归原权利人所有，项目代码的 MIT 许可不覆盖该商品图片；不表示麦当劳官方背书。

分章节数据叙事参考[网易云音乐 2024 年度听歌报告介绍](https://socialbeta.com/campaign/23649)及[B站 2024 年度报告官方介绍](https://www.bilibili.com/opus/1012957518392983553)。页面、文案规则与排版为本项目实现，未使用这些平台的页面代码、插画或标识。

## 优惠门槛解析

`mcp_adapter.py::extract_threshold_gap` 改编自 [mcd-coupon-strategist](https://github.com/Ayzzz123/mcd-coupon-strategist/blob/e391fd00ad7322ddddd5d15d88c403c5459f0f5d/src/mcd_adapter.py) 的同名函数，提交 e391fd00ad7322ddddd5d15d88c403c5459f0f5d。
保留其 balance 为分、realDiscount 为元的字段语义；改用整数分、严格金额校验、缺失值保留与“尚未享受”标志，不按宣传数额重算应付金额。
券专属商品码、一单一券和同ID多券码的调用规则参考该项目 MCP_INTEGRATION.md；prepare-order 与本地预览/确认流程为本项目实现。参考项目不是独立核销服务，真实扣券由官方下单处理。

以下为原项目完整许可：

MIT License

Copyright (c) 2026 麦麦省钱助手 contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
