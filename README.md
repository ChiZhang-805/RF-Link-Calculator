# RF-Link-Calculator

本地中文射频链路计算器：编辑器件链路，计算增益、噪声、IP3/IM3、压缩和动态范围；支持 S 参数失配、实测功率曲线、混频杂散、IQ 记忆模型与外部对标。

当前应用 **2.3.0**。新增 Windows EXE / 安装包、注册登录、密码恢复、账户项目隔离，以及 Docker 云端部署配置。

[Windows 下载](https://github.com/ChiZhang-805/RF-Link-Calculator/releases/latest) · [公开入口](https://chizhang-805.github.io/RF-Link-Calculator/) · [账户、安装与部署](docs/构建发布与部署.md)

计算内核延续 2.2.0：保留串行链路预算、实射频行为模型谐波平衡、失配、镜像噪声、IQ 与对标；新增独立的晶体管电路页：RC 网络、本征 NPN Ebers–Moll、直流/谐波、周期线性化白噪声、稳定性及截断检查。有限模型范围和独立验证见 [晶体管与周期噪声实现验证](docs/晶体管与周期噪声实现验证.md)。32 项实际 ngspice 对照通过；目标器件、商用周期噪声与硬件精度仍待验收。此版本不等同于完整 SPICE/ADS/SpectreRF，原生 VSDX 仍待目标 Visio 环境验证。原有功能见 [谐波与镜像实现验证](docs/谐波与镜像实现验证.md) 和 [V2 模型实现与交接](docs/V2模型实现与交接.md)。

## 启动

桌面版下载安装包后启动并注册本机账户。云端部署准备已完成，真实邮件与公网计算待配置云服务和 SMTP。GitHub Pages 仅提供下载入口。

带账户的源码入口：安装 `requirements-portal.txt` 和本包后，运行 `python -m rf_link_calculator.portal`，打开 `http://127.0.0.1:8510`。下列为兼容保留的无账户本地开发入口，不能转发到公网。

已配置的本机环境可直接运行：

```powershell
cd Q:\CodexData\Workspaces\RF-Link-Calculator
.\scripts\start.ps1
```

打开 <http://127.0.0.1:8501>。此路径遵循本次Q盘规则；程序没有硬编码这个盘符。

新环境建议使用已验证的 **Python 3.13 x64**：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
$env:RF_LINK_DATA_DIR = Join-Path $PWD 'outputs'
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe -m rf_link_calculator.presentation.server
```

Linux/macOS使用`.venv/bin/python`及`export RF_LINK_DATA_DIR=...`。内核支持Python 3.11+，锁文件按本次Windows/Python 3.13实测环境生成；其他组合未做正式验收。Graphviz的`dot`程序另装；缺少它仍可使用确定性串行布局、宽图和分段图。端口冲突时运行`./scripts/start.ps1 -Port 8502`。

上述旧开发入口只监听127.0.0.1，不需要账号，不上传项目参数，无运行时外部CDN。默认数据目录是用户主目录下的`RF-Link-Calculator-Data`，启动脚本改为本工程`outputs`，也可由环境变量或设置对话框指定。源码与项目数据分开管理。

## 使用

1. 载入构造示例或导入JSON／规范CSV，填写频率、带宽、源温度与主CW输入。
2. 在表格编辑器件；使用右上角添加、行末复制和删除管理器件，拖动行左侧手柄调整当前页顺序。器件固定每页五个，通过上一页、下一页切换；稳定ID防止参数串行。
3. 在右侧逐行编辑参数，通过“频率 / 来源”页签，填写物理温度、频率范围、LO、噪声口径、压缩形状、额定值和来源条件。未知参数保持“未知”，不要改成理想来消除告警。
4. 点“计算”，查看系统卡片、逐级表、信号／噪声曲线、压缩扫描和框图。修改输入后结果标为过期，必须重算才能导出。
5. 保存项目JSON，或生成工程ZIP。ZIP内有输入、结果、可重算XLSX、SVG、原生draw.io、Visio说明、假设告警与SHA-256清单。严格报告模式阻止错误及未解决的频率／来源条件告警。

旧开发入口的JSON文件保存采用原子替换，保留最近5份自动备份；账户入口按账户保存到SQLite。项目导入上限32 MiB、200级、单段文字4096字符；兼容 schema 1.0.0 和 2.0.0，更高版本被拒绝。单次模型文件上限8 MiB。旧器件CSV仅用于标量项目，不能丢弃高级模型后导出；XLSX不是项目回读格式。高级工程包中的工作簿为结果快照，标量项目仍为可重算公式表。

默认使用固定视口工作台：链路、分析、框图三个工作区；右侧标签与输入一行一个，可用滚轮查看后续参数，不显示滚动条。窄窗口改用参数抽屉；器件表按可用高度分页。Ctrl+Enter计算、Ctrl+S保存、Ctrl+O打开；不在输入框内时Ctrl+Z/Y撤销重做。

射频图使用共享黑白功能符号，名称置于图形外；官方软件参照与适用边界见[射频符号约定](docs/射频符号约定.md)。旧Streamlit入口 `python -m streamlit run app.py` 保留供兼容，不是默认工作台。

2.0.1 已加入两个公开商用算例，完成厂商 S 参数、公开数值参考及实测 IQ 留出验证，详见 [公开数据验证记录](docs/公开数据验证记录.md)。这不等于已在本机运行商用求解器或验收目标硬件。

## 公式与状态

噪声采用290 K参考Friis公式，源温度与无源器件物理温度独立。实际输出P1转输入时包含额外1 dB；线性外推输出参考另列。IP3采用相干最坏相位弱非线性预算。压缩使用以P1为锚点的单调软饱和模型，逐级传播后求整链总压缩1 dB的根，默认p=2。

旧标量预算的 IP3 与压缩模型独立。高级模型的双音/多音由复包络逐级传播后做 FFT，AM/AM–AM/PM 或 IQ 记忆模型决定波形失真；独立 IP3 输入仍用于小信号预算。未满足模型条件时拒绝计算或返回未知。缺NF不阻断增益；缺P1不阻断小信号噪声；理想和未知分别显示。负动态范围保留并告警。空链路为恒等传输且提示尚无器件。

详细说明：[公式与模型](docs/公式与模型说明.md)、[Excel使用说明](docs/Excel使用说明.md)、[验收记录](docs/验收记录.md)、[需求追踪](docs/需求追踪.md)。

## 验证与当前环境

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src scripts tests app.py
.\.venv\Scripts\python.exe scripts/export_example.py --example 接收链路
```

V2 自动化检查及浏览器验证见 [V2验证记录](docs/V2验证记录.md)。覆盖独立网络对照、噪声解析、功率曲线、双音 FFT、IQ 留出验证、数据校验、对标与导出，旧65项回归继续保留。此前桌面重算实际引擎为 **WPS表格12.1.0.29161**，26个标量场景与Python数值一致。虽然其COM接口自报“Microsoft Excel”，实际进程为`et.exe`，因此 **Microsoft Excel兼容性尚未验收**。旧WPS结果不代表高级模型或商用RF工具验收。

可选桌面检查命令为`python scripts/verify_excel_desktop.py`，需要Windows与pywin32；脚本记录真实程序路径、隔离生成文件、完整重算、保存后读缓存对照，并设置超时。不会访问用户已有工作簿。

draw.io网页版已用构造样例检查不同器件符号、中文、移动混频器后的主线／LO随动、文字编辑、浏览器本地保存并重新加载。SVG为标准矢量；不保证插入任意Visio版本后自动成为原生粘接图形。原生VSDX未生成。

## 人工交接

- 提供目标商用软件的合法运行环境或独立导出结果，执行现成对标包；当前没有已完成的商用精度对标。
- 提供实际器件模型、校准测量数据与独立留出数据，并确定允许误差。五套高级示例均为合成验证工程。
- 在真正的Microsoft Excel目标版本运行桌面重算验收；当前机器COM由WPS接管。
- 若需要原生VSDX，提供可用且已授权的Visio目标环境，再验证原生形状、连接器粘接及保存重开。
- 真实硬件数据、SSB/DSB口径、频点／偏置／温度和压缩模型适合度需要射频工程确认。示例均为构造值。
- 公开仓库已获项目所有者授权；具体开源许可仍待所有者决定，公开可见不等于授予开源许可。

