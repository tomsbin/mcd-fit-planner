# 第三方来源及许可

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
