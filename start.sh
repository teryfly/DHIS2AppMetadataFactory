#!/usr/bin/env bash
#
# DAMF 启停脚本 —— 配合 install.sh 使用。
#
#   ./start.sh                      启动（沿用上次渲染的端口与模式）
#   ./start.sh --port 9443          换端口并生效（会重渲染 nginx 并重载）
#   ./start.sh --auth off --providers fakes
#   ./start.sh --seed               启动后灌一个演示项目
#   ./start.sh --stop / --restart / --status / --logs
#
# 端口/模式若不给，就从已渲染的 .deploy/nginx.conf 与 .deploy/env 里读回，所以裸跑不会把
# install.sh 指定的端口重置成默认值。

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$REPO/.deploy"
ENV_FILE="$DEPLOY/env"
TLS="$DEPLOY/tls"
VENV="$REPO/S1/.venv"
SERVICE=damf-api
WORKER_SERVICE=damf-worker

ACTION=start
PORT=""
MOBILE_PORT=""
API_PORT=""
PUB_IP=""
DOMAIN=""
AUTH=""
PROVIDERS=""
SEED=0
RENEW_TLS=0
DRY_RUN=0
NO_RECONFIGURE=0
CONFIG_DIRTY=0

C_HEAD=$'\e[1;36m'
C_OK=$'\e[1;32m'
C_WARN=$'\e[1;33m'
C_ERR=$'\e[1;31m'
C_OFF=$'\e[0m'

step() { printf '\n%s▸ %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
bad() { printf '  %s✗%s %s\n' "$C_ERR" "$C_OFF" "$*"; }
die() { printf '\n%s中止：%s%s\n' "$C_WARN" "$*" "$C_OFF" >&2; exit 1; }

priv() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '  %s·%s [dry-run] %s\n' "$C_WARN" "$C_OFF" "$*"
  elif [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}
must_priv() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

usage() {
  cat <<'EOF'
用法：./start.sh [动作] [开关]

动作：
  （默认）        启动
  --restart       重启      --stop  停止      --status  状态      --logs  最近日志

开关：
  --port 端口           S5 桌面控制台对外端口
  --mobile-port 端口    S5M 移动控制台对外端口
  --api-port 端口       factory-api 本机端口（只监听 127.0.0.1）
  --ip 地址             对外 IP/主机名（变了会重签证书 SAN）
  --hostname 域名       额外域名
  --auth local|off      登录模式
  --providers auto|fakes|real 后端能力
  --seed                启动后灌一个演示项目
  --renew-tls           强制重签自签证书
  --no-reconfigure      只启停服务，不重渲染 nginx/systemd
  --dry-run             不执行特权动作
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --restart) ACTION=restart; shift ;;
    --stop) ACTION=stop; shift ;;
    --status) ACTION=status; shift ;;
    --logs) ACTION=logs; shift ;;
    --port) CONFIG_DIRTY=1; PORT="${2:?--port 需要值}"; shift 2 ;;
    --mobile-port) CONFIG_DIRTY=1; MOBILE_PORT="${2:?--mobile-port 需要值}"; shift 2 ;;
    --api-port) CONFIG_DIRTY=1; API_PORT="${2:?--api-port 需要值}"; shift 2 ;;
    --ip) CONFIG_DIRTY=1; PUB_IP="${2:?--ip 需要值}"; shift 2 ;;
    --hostname) CONFIG_DIRTY=1; DOMAIN="${2:?--hostname 需要值}"; shift 2 ;;
    --auth) CONFIG_DIRTY=1; AUTH="${2:?--auth 需要值}"; shift 2 ;;
    --providers) CONFIG_DIRTY=1; PROVIDERS="${2:?--providers 需要值}"; shift 2 ;;
    --seed) SEED=1; shift ;;
    --renew-tls) RENEW_TLS=1; CONFIG_DIRTY=1; shift ;;
    --no-reconfigure) NO_RECONFIGURE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "未知参数：$1（--help 看用法）" ;;
  esac
done

[ -f "$ENV_FILE" ] || die "没有 $ENV_FILE —— 先跑 ./install.sh"

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }

# 未指定的开关一律回读已生效的配置，避免裸跑把端口重置成默认值。
if [ -f "$DEPLOY/nginx.conf" ]; then
  mapfile -t rendered_ports < <(grep -oE '^[[:space:]]*listen +[0-9]+ ssl' "$DEPLOY/nginx.conf" | grep -oE '[0-9]+')
  [ -n "$PORT" ] || PORT="${rendered_ports[0]:-8443}"
  [ -n "$MOBILE_PORT" ] || MOBILE_PORT="${rendered_ports[1]:-8444}"
else
  PORT="${PORT:-8443}"; MOBILE_PORT="${MOBILE_PORT:-8444}"
fi
API_PORT="${API_PORT:-$(env_get FACTORY_API_PORT)}"; API_PORT="${API_PORT:-8080}"
AUTH="${AUTH:-$(env_get FACTORY_AUTH_MODE)}"; AUTH="${AUTH:-local}"
PROVIDERS="${PROVIDERS:-$(env_get FACTORY_PROVIDER_MODE)}"; PROVIDERS="${PROVIDERS:-auto}"
if [ -z "$PUB_IP" ]; then
  PUB_IP="$(grep -oE 'IP:[0-9.]+' "$TLS/san.txt" 2>/dev/null | head -1 | cut -d: -f2)"
  PUB_IP="${PUB_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
  PUB_IP="${PUB_IP:-127.0.0.1}"
fi

api_base="http://127.0.0.1:${API_PORT}"

show_status() {
  step "状态"
  must_priv systemctl is-active --quiet "$SERVICE" && ok "$SERVICE 运行中" || bad "$SERVICE 未运行"
  must_priv systemctl is-active --quiet "$WORKER_SERVICE" && ok "$WORKER_SERVICE 运行中" || bad "$WORKER_SERVICE 未运行（周期任务不会跑）"
  must_priv systemctl is-active --quiet nginx && ok "nginx 运行中" || bad "nginx 未运行"
  listening="$(ss -ltn 2>/dev/null | awk -v a="$API_PORT" -v p="$PORT" -v m="$MOBILE_PORT" \
    'NR>1 {split($4,x,":"); port=x[length(x)]; if ((port==a||port==p||port==m) && !seen[port]++) printf ":%s ", port}')"
  printf '  监听端口   %s\n' "${listening:-无}"
  curl -s -m 5 "$api_base/healthz" >/dev/null 2>&1 && ok "GET $api_base/healthz 200" || bad "GET $api_base/healthz 不通"
  for p in "$PORT" "$MOBILE_PORT"; do
    code="$(curl -sk -m 6 -o /dev/null -w '%{http_code}' "https://127.0.0.1:${p}/" 2>/dev/null || echo 000)"
    [ "$code" = "200" ] && ok "https://127.0.0.1:${p}/ → 200" || bad "https://127.0.0.1:${p}/ → ${code}"
  done
}

case "$ACTION" in
  stop)
    step "停止"
    priv systemctl stop "$WORKER_SERVICE"
    priv systemctl stop "$SERVICE"
    ok "$SERVICE 与 $WORKER_SERVICE 已停止（nginx 仍在跑，只是没有后端）"
    exit 0
    ;;
  status)
    show_status
    exit 0
    ;;
  logs)
    # UNIT=damf-worker ./start.sh --logs 只看一个单元；默认两个都打。
    for unit in ${UNIT:-$SERVICE $WORKER_SERVICE}; do
      printf '%s══ %s ══%s\n' "$C_HEAD" "$unit" "$C_OFF"
      must_priv journalctl -u "$unit" --no-pager -n "${LOGLINES:-60}"
    done
    exit 0
    ;;
esac

# ---------------------------------------------------------------- 配置同步

if [ "$NO_RECONFIGURE" = "0" ]; then
  step "同步配置"
  if [ "$AUTH" = "local" ] && [ -z "$(env_get FACTORY_BOOTSTRAP_ADMIN_PASSWORD)" ]; then
    gen="Damf-$(openssl rand -hex 6)"
    printf 'FACTORY_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$gen" >>"$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "引导管理员口令已生成（见最后）"
  fi
  # 界面录入凭证的主密钥（FR-SEC-006）。早于这一版生成的 env 文件里没有这一行，补上即可用；
  # 补过之后绝不再动——换一个主密钥会让库里每个已存凭证解不开（keyMismatch），要人逐个重录。
  if ! grep -qE '^FACTORY_CREDENTIAL_MASTER_KEY=' "$ENV_FILE" 2>/dev/null; then
    printf 'FACTORY_CREDENTIAL_MASTER_KEY=%s\n' "$(openssl rand -hex 32)" >>"$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "凭证主密钥已生成（界面录入凭证由此可用）"
  fi
  args=(--config-only --port "$PORT" --mobile-port "$MOBILE_PORT" --api-port "$API_PORT"
        --auth "$AUTH" --providers "$PROVIDERS" --ip "$PUB_IP")
  [ -n "$DOMAIN" ] && args+=(--hostname "$DOMAIN")
  [ "$RENEW_TLS" = "1" ] && args+=(--renew-tls)
  [ "$DRY_RUN" = "1" ] && args+=(--dry-run)
  bash "$REPO/install.sh" "${args[@]}" | sed 's/^/  /'
fi

# ---------------------------------------------------------------- 启动

step "启动 $SERVICE"
running="$(must_priv systemctl is-active --quiet "$SERVICE" && echo yes || echo no)"
needs_restart=0
[ "$ACTION" = "restart" ] && needs_restart=1
[ "$CONFIG_DIRTY" = "1" ] && needs_restart=1
# .deploy/env 比进程还新 = 手工改过配置没生效；systemd 的 start 对已运行进程是空操作。
if [ "$running" = "yes" ]; then
  pid="$(must_priv systemctl show "$SERVICE" -p MainPID --value 2>/dev/null || true)"
  if [ -n "${pid:-}" ] && [ -d "/proc/$pid" ] && [ "$ENV_FILE" -nt "/proc/$pid" ]; then
    needs_restart=1
  fi
else
  needs_restart=1
fi
if [ "$needs_restart" = "1" ]; then
  ok "$([ "$running" = "yes" ] && echo "配置有变，重启" || echo "启动")"
  priv systemctl restart "$SERVICE"
else
  ok "已在运行且配置未变，保持不动"
fi
[ "$DRY_RUN" = "1" ] && { warn "dry-run：跳过健康检查"; exit 0; }

printf '  等待 /healthz'
ready=0
for _ in $(seq 1 60); do
  if curl -s -m 3 "$api_base/healthz" >/dev/null 2>&1; then ready=1; break; fi
  printf '.'
  sleep 1
done
echo
[ "$ready" = "1" ] || {
  bad "60s 内没有就绪，最近日志："
  must_priv journalctl -u "$SERVICE" --no-pager -n 30 | sed 's/^/    /' >&2
  die "factory-api 未起来（上面是日志尾部）"
}
ok "factory-api 就绪"

# ---------------------------------------------------------------- 跟随：worker
# 迁移没有并发锁，worker 也在启动时跑同一批迁移——所以它必须在 api 过 /healthz 之后才起。
# 同一个 EnvironmentFile：api 因配置变化重启了，worker 也要用新配置重启一次。
step "启动 $WORKER_SERVICE"
w_running="$(must_priv systemctl is-active --quiet "$WORKER_SERVICE" && echo yes || echo no)"
if [ "$ACTION" = "restart" ] || [ "$CONFIG_DIRTY" = "1" ] || [ "$needs_restart" = "1" ] || [ "$w_running" != "yes" ]; then
  priv systemctl restart "$WORKER_SERVICE"
  ok "$WORKER_SERVICE 已$([ "$w_running" = "yes" ] && echo 重启 || echo 启动)（周期任务开始运行）"
else
  ok "$WORKER_SERVICE 已在运行且配置未变，保持不动"
fi

# ---------------------------------------------------------------- 自检

step "自检"
mode="$(curl -s -m 5 "$api_base/healthz" | sed -n 's/.*"authMode":"\([^"]*\)".*/\1/p')"
if [ "$mode" = "$AUTH" ]; then ok "authMode=${mode}"; else bad "authMode=${mode:-未知}，期望 ${AUTH}"; fi
providers_line="$(curl -s -m 5 "$api_base/healthz" | tr ',' '\n' | sed -n 's/.*"S[234]":"\([^"]*\)".*/\1/p' | paste -sd/ -)"
ok "提供方：${providers_line:-未知}"

if [ "$AUTH" = "local" ]; then
  login_body="$(ADMIN_SUBJECT="$(env_get FACTORY_BOOTSTRAP_ADMIN_SUBJECT)" ADMIN_PASSWORD="$(env_get FACTORY_BOOTSTRAP_ADMIN_PASSWORD)" \
    python3 -c 'import json,os;print(json.dumps({"subject":os.environ["ADMIN_SUBJECT"],"password":os.environ["ADMIN_PASSWORD"]}))')"
  login="$(curl -s -m 8 -X POST "$api_base/api/v1/auth/login" -H 'Content-Type: application/json' -d "$login_body")"
  if printf '%s' "$login" | grep -q '"authenticated":true'; then
    ok "管理员可登录（会话 Cookie 是 Secure 的，只能经 HTTPS 在浏览器里用）"
  else
    bad "管理员登录失败：${login:0:160}"
  fi
else
  ok "auth_mode=off：界面里填操作人姓名即可，无账号口令"
fi

for p in "$PORT" "$MOBILE_PORT"; do
  code="$(curl -sk -m 6 -o /dev/null -w '%{http_code}' "https://127.0.0.1:${p}/" 2>/dev/null || echo 000)"
  api_code="$(curl -sk -m 6 -o /dev/null -w '%{http_code}' "https://127.0.0.1:${p}/api/v1/auth/session" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ] && [ "$api_code" != "000" ]; then
    ok "https://…:${p} 静态 200、同源 /api/v1 通（${api_code}）"
  else
    bad "https://…:${p} 静态 ${code}、api ${api_code}"
  fi
done

# ---------------------------------------------------------------- 演示数据

if [ "$SEED" = "1" ]; then
  step "灌演示项目"
  API_BASE="$api_base" AUTH_MODE="$AUTH" \
    ADMIN_SUBJECT="$(env_get FACTORY_BOOTSTRAP_ADMIN_SUBJECT)" \
    ADMIN_PASSWORD="$(env_get FACTORY_BOOTSTRAP_ADMIN_PASSWORD)" \
    "$VENV/bin/python" - <<'PY' || warn "演示数据未灌入（服务本身可用）"
import os, sys, time
import httpx

base = os.environ["API_BASE"] + "/api/v1"
mode = os.environ.get("AUTH_MODE", "off")
name = "demo-outpatient-" + time.strftime("%m%d-%H%M")
client = httpx.Client(base_url=base, timeout=30)

if mode != "off":
    r = client.post("/auth/login", json={"subject": os.environ["ADMIN_SUBJECT"], "password": os.environ["ADMIN_PASSWORD"]})
    if r.status_code != 200:
        sys.exit(f"login {r.status_code}: {r.text[:160]}")
    cookie = "; ".join(h.split(";")[0] for h in r.headers.get_list("set-cookie") if h.startswith("factory_"))
    # 会话 Cookie 是 Secure 的，http 客户端默认不回传，这里显式命名。
    client.headers.update({"Cookie": cookie, "X-CSRF-Token": r.json()["csrfToken"]})
else:
    client.headers.update({"X-Operator": "demo"})

# 项目的目标环境必须是已注册的环境，否则 S1 直接 INVALID_STATE。
envs = client.get("/environments").json().get("items", [])
if not envs:
    print("  ! 未灌入：还没有任何环境。打开界面左侧的「开始使用」（S5M 是 /start）按提示登记一台，"
          "或改用 ./start.sh --providers fakes --seed")
    sys.exit(0)
targets, ref_used = [], False
for i, e in enumerate(envs):
    is_prod = (e.get("environmentClass") or "") == "PRODUCTION"
    design = (not is_prod) and not ref_used
    ref_used = ref_used or design
    targets.append({"environmentKey": e["environmentKey"], "promotionOrder": (i + 1) * 100, "isDesignReference": design})
if not ref_used:
    print("  ! 未灌入：注册的环境全是 PRODUCTION，演示项目需要一个非生产环境（CP-PRJ-002，与「开始使用」清单第 2 行同一件事）")
    sys.exit(0)

project = client.post("/projects", json={
    "name": name, "ownerName": "demo", "description": "seeded by start.sh --seed", "deliveryMode": "A",
    "targets": targets,
}, headers={"Idempotency-Key": f"seed-{name}"})
project.raise_for_status()
view = project.json()
upload = client.post(
    f"/projects/{view['id']}/inputs",
    files={"file": ("requirements.md", "# 需求\n\n门诊随访看板：按医生、按周统计随访完成情况。\n", "text/markdown")},
    data={"dataClassification": "INTERNAL"},
)
upload.raise_for_status()
run = client.post(f"/stages/{view['currentStageId']}/run", json={})
print(f"  ✓ 项目 {view['id']} · 工作流 {view['workflowId']} · 阶段 {run.json().get('status', '已提交')}")
PY
fi

# ---------------------------------------------------------------- 打印访问信息

cat <<EOF

${C_OK}已启动${C_OFF}
  S5  桌面控制台   https://${PUB_IP}:${PORT}/
  S5M 移动控制台   https://${PUB_IP}:${MOBILE_PORT}/
  API              127.0.0.1:${API_PORT}（同源反代为 /api/v1）
  后端能力         ${PROVIDERS}   登录模式 ${AUTH}
EOF
if [ "$AUTH" = "local" ]; then
  cat <<EOF
  登录账号         $(env_get FACTORY_BOOTSTRAP_ADMIN_SUBJECT) / $(env_get FACTORY_BOOTSTRAP_ADMIN_PASSWORD)
                   （口令在 $ENV_FILE，0600；换掉后 ./start.sh --restart）
EOF
fi
printf '  证书           %s（自签，浏览器首次访问点「继续访问」）\n' "$TLS/cert.pem"
printf '  日志           ./start.sh --logs      停止 ./start.sh --stop\n'
printf '  提醒           云安全组/防火墙要放行 %s 与 %s\n\n' "$PORT" "$MOBILE_PORT"
