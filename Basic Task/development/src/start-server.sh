#!/usr/bin/env bash
# ============================================================================
# 启动 llama-server（基线配置）
#
#   * 自动读取 GGUF 元数据，识别模型名与量化档位
#   * 日志按模型自动命名：~/server-<模型标识>.log
#   * 关闭主机 RAM prompt cache（--no-cache-prompt --cache-ram 0），
#     使缓存工作集全程驻留显存（任务要求）
#   * 启动前彻底清理旧进程并等待端口释放，避免端口绑定失败
#   * 前台 exec 运行：Ctrl+C 可干净停止；关闭窗口不会留下残留进程
#
# 用法：  bash ~/start-server.sh
#         bash ~/start-server.sh /path/to/other-model.gguf    # 换模型
# 停止：  在运行中的终端按 Ctrl+C
# 看日志：tail -f ~/server-<模型标识>.log
#
# 对应配置：configs/service_baseline.json
# ============================================================================
set -euo pipefail

# ---- 模型文件（唯一需要手动指定的东西）------------------------------------
MODEL_FILE="${1:-/mnt/d/LLM/blobs/sha256-2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730}"

# ---- 环境 ------------------------------------------------------------------
LLAMA_DIR="$HOME/llama/llama-b11540"
GGUF_INFO="$HOME/gguf-info.py"

# ---- 服务参数（与 configs/service_baseline.json 保持一致）-----------------
NGL=99            # 全部层卸载到 GPU
LOAD_MODE="none"  # 等价于旧版 --no-mmap：权重不做内存映射
CTX=24576         # 配置总上下文
NP=2              # slot 数（每 slot = CTX / NP = 12288）
FLASH="on"        # Flash Attention
BATCH=2048        # 逻辑批大小
UBATCH=512        # 物理批大小
HOST="127.0.0.1"
PORT=8080

# ---- 前置检查 --------------------------------------------------------------
[[ -x "$LLAMA_DIR/llama-server" ]] || { echo "错误：找不到 $LLAMA_DIR/llama-server" >&2; exit 1; }
[[ -f "$MODEL_FILE" ]]           || { echo "错误：找不到模型文件：$MODEL_FILE" >&2; exit 1; }

# ---- 自动识别模型信息（读取 GGUF 元数据）----------------------------------
MM_NAME="未知"; MM_ARCH=""; MM_SIZE=""; MM_QUANT=""; MM_FILETYPE=""; MM_TAG="model"
if [[ -f "$GGUF_INFO" ]]; then
  if MM_OUT="$(python3 "$GGUF_INFO" "$MODEL_FILE" 2>/dev/null)"; then
    eval "$MM_OUT"
  else
    echo "警告：GGUF 元数据读取失败，改用文件名推导标识。" >&2
    MM_TAG="$(basename "${MODEL_FILE%.*}")"; MM_NAME="$MM_TAG"
  fi
else
  echo "警告：未找到 $GGUF_INFO，改用文件名推导标识。" >&2
  MM_TAG="$(basename "${MODEL_FILE%.*}")"; MM_NAME="$MM_TAG"
fi

LOG_FILE="$HOME/server-${MM_TAG}.log"
MODEL_BYTES="$(stat -c %s "$MODEL_FILE" 2>/dev/null || echo 0)"
MODEL_SHA256="$(basename "$MODEL_FILE" | sed -n 's/^sha256-\([0-9a-f]\{64\}\)$/\1/p')"

# ---- 打印识别结果 ----------------------------------------------------------
echo "=============================================================="
echo "模型      : $MM_NAME"
[[ -n "$MM_SIZE" ]]  && echo "参数规模  : $MM_SIZE"
[[ -n "$MM_ARCH" ]]  && echo "架构      : $MM_ARCH"
[[ -n "$MM_QUANT" ]] && echo "量化      : $MM_QUANT（GGUF file_type=$MM_FILETYPE）"
[[ -n "$MODEL_SHA256" ]] && echo "SHA256    : $MODEL_SHA256"
echo "模型文件  : $MODEL_FILE"
echo "文件大小  : $((MODEL_BYTES / 1024 / 1024)) MiB"
echo "标识      : $MM_TAG"
echo "引擎      : $LLAMA_DIR"
echo "服务参数  : -ngl $NGL -lm $LOAD_MODE -c $CTX -np $NP -fa $FLASH -b $BATCH -ub $UBATCH"
echo "缓存设置  : --no-cache-prompt --cache-ram 0（关闭主机 RAM prompt cache）"
echo "每 slot   : $((CTX / NP)) tokens"
echo "监听      : http://$HOST:$PORT"
echo "日志      : $LOG_FILE"
echo "=============================================================="

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "GPU（启动前）："
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
else
  echo "警告：nvidia-smi 不可用，GPU 可能未挂载。" >&2
fi

# ---- 清理旧进程：等待其真正退出 --------------------------------------------
if pgrep -f llama-server >/dev/null 2>&1; then
  echo "发现已有 llama-server，正在停止…"
  pkill -f llama-server || true

  # 最多等 30 秒，逐秒检查进程是否退出
  for _ in $(seq 1 30); do
    if pgrep -f llama-server >/dev/null 2>&1; then
      sleep 1
    else
      break
    fi
  done

  # 仍未退出则强制终止
  if pgrep -f llama-server >/dev/null 2>&1; then
    echo "进程未响应，强制终止…"
    pkill -9 -f llama-server || true
    sleep 3
  fi
fi

# ---- 等待端口释放（最多 30 秒）--------------------------------------------
for _ in $(seq 1 30); do
  if ss -lnt 2>/dev/null | grep -q ":${PORT} "; then
    sleep 1
  else
    break
  fi
done
if ss -lnt 2>/dev/null | grep -q ":${PORT} "; then
  echo "错误：端口 ${PORT} 仍被占用，占用者如下，请手动处理后重试：" >&2
  ss -lntp | grep ":${PORT} " >&2
  exit 1
fi
echo "旧进程与端口均已清理，开始启动…"
echo "就绪标志：server is listening on http://$HOST:$PORT"
echo "--------------------------------------------------------------"

# ---- 启动（前台 exec：Ctrl+C 可干净停止）----------------------------------
# 注意：不使用管道 | tee —— 管道会让 exec 只替换子 shell，
#       导致服务成为后台残留进程，关闭终端后仍占用端口与显存。
cd "$LLAMA_DIR"
exec ./llama-server \
  -m "$MODEL_FILE" \
  -ngl "$NGL" -lm "$LOAD_MODE" -c "$CTX" -np "$NP" -fa "$FLASH" \
  -b "$BATCH" -ub "$UBATCH" \
  --no-cache-prompt --cache-ram 0 \
  --host "$HOST" --port "$PORT" > "$LOG_FILE" 2>&1
