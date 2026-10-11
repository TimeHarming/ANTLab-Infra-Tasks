# 源码

在本目录编写性能测试、文档输入处理、文件存储、前端展示和服务管理代码，内部目录由学生设计。

请保存依赖声明及必要的运行入口，在开发区域首页说明启动流程和模块职责。

`提示词与输入文档/输入文档/编程输入03-ticket_stats.py` 是模型测试素材。模型生成的修复版本和相关检查结果按运行编号保存到 `results/`，用于比较测试配置的输出效果。




<!-- ================= 以下是本项目实现说明（学生添加） ================= -->

# 实现说明

## 一、模块职责

| 文件 | 职责 | 对应上文分类 | 状态 |
| --- | --- | --- | --- |
| `input_loader.py` | 读取素材（UTF-8）、计算 SHA256、按「系统消息 → 资料区 → 任务区」组装 messages、统计输入 token 数 | 文档输入处理 | ✅ |
| `metrics.py` | 由逐请求记录汇总 TTFT / TPOT / 吞吐 / 中位数 / P95 / 成功率 | 性能测试 | ✅ |
| `server_client.py` | 流式调用 `/v1/chat/completions`、采集客户端时间戳、按真实 token 数统计 | 性能测试 | ⏳ |
| `monitor.py` | 采集 GPU 显存（加载后 / 测试峰值）与加载日志 | 性能测试 | ⏳ |
| `storage.py` | 按运行编号落盘 `config.json` / `requests.jsonl` / `summary.json` / `outputs/` | 文件存储 | ⏳ |
| `service_manager.py` | 健康检查、异常记录、进程重启、就绪确认、有上限的重试 | 服务管理 | ⏳ |
| `runner.py` / `run.py` | 主控编排与运行入口 | 运行入口 | ⏳ |
| `web/` | 前端展示：服务状态、请求状态、模型输出、性能统计、基线对比 | 前端展示 | ⏳ |
| `requirements.txt` | 依赖声明 | 依赖声明 | ⏳ |

## 二、运行入口

```bash
# 输入组装自检（不需要推理服务）
python3 -B input_loader.py

# 性能指标自检（纯函数，不需要推理服务）
python3 -B metrics.py

# 正式运行入口（run.py）随 runner.py 一并提供，见 ../README.md 的运行方法一节。

---

## 三、关键实现说明

### input_loader.py

1. **组装顺序**：按测试提示词规定固定为「系统消息 → 用户消息中的资料区 → 任务区」，资料区与任务区拼接在同一条 `user` 消息内。
2. **为什么资料在前**：同一份素材配不同任务的请求，token 前缀完全一致，便于服务端复用 KV 缓存、减少重复计算。
3. **SHA256 的计算方式**：先以二进制读取原始字节再解码为文本，避免换行符或编码转换影响校验值。
4. **token 计数的来源标注**：优先调用服务端 `/tokenize`（使用模型真实分词器）得到 `source=server`；服务不可用时降级为字符数估算并标注 `source=estimated`，报告中只引用 `server` 来源的数值。
5. **实测校验**：`编程输入03-ticket_stats.py` 经 `/tokenize` 计得 1518 tokens，与任务文档以 `Qwen3-0.6B` tokenizer 核验的 1518 一致。

### metrics.py

1. **TPOT 口径**：`(完成时刻 − 首 token 时刻) ÷ (输出 token 数 − 1)`，**排除首 token**。理由是首 token 之前的耗时主要来自提示词处理（prefill），属于 TTFT 范畴，计入会污染生成速度的度量。该口径写入 `summary.json` 的 `definitions` 字段以便复核。
2. **百分位算法**：最近秩法，位置 = `ceil(k / 100 × 样本数)`；样本为空返回 `None`，样本量为 1 时返回该样本本身。
3. **吞吐量口径**：仅统计成功请求的输出 token 总数，除以统计窗口（最早提交 → 最晚完成）。
4. **状态四态**：`success` / `failed` / `timeout` / `cancelled`，缺失或无法识别的状态按 `failed` 处理；成功率的分母为全部请求数。
5. **内建自检**：用 12 条构造记录（8 快 + 2 慢 + 1 超时 + 1 取消）验证各公式，运行 `python3 -B metrics.py` 可复现，10 项检查全部通过。

## 四、与 results/ 的对应关系

| 本目录模块 | 产出物 | 落盘位置 |
| --- | --- | --- |
| `server_client.py` | 逐请求记录（时间戳、token 数、状态、错误） | `../results/<运行编号>/requests.jsonl` |
| `metrics.py` | 汇总统计（中位数/P95/吞吐/成功率/口径定义） | `../results/<运行编号>/summary.json` |
| `storage.py` | 服务配置快照 | `../results/<运行编号>/config.json` |
| — | 每个请求的模型输出文本 | `../results/<运行编号>/outputs/` |