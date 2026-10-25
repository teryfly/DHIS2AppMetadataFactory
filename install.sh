#!/usr/bin/env bash
#
# DAMF（元数据工厂）安装脚本 —— 在一台干净的 Ubuntu/Debian 服务器上装出可外网访问的一套：
#
#   PostgreSQL ── factory-api（S1 控制平面，只监听 127.0.0.1）──┐
#   nginx（TLS，同源反代 /api/v1 + SSE）── S5 桌面控制台  ──────┤
#                          └──────────────────────── S5M 移动端 ┘
#
# 用法 ./install.sh --ip 43.132.224.225 --port 8443 --mobile-port 8444
#      ./install.sh --help 看全部开关；幂等，可反复执行。装完用 ./start.sh 启动。
#
# 为什么必须有 TLS：S1 的会话 Cookie 写死 secure=True
# （S1/src/factory_control/rest/routes/identity.py 的 _set_session），浏览器只在 HTTPS 或
# http://localhost 下才保存它 —— 纯 http://公网IP 与「账号密码登录」不可兼得。

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$REPO/.deploy"
STATE="$DEPLOY/state"
TLS="$DEPLOY/tls"
VENV="$REPO/S1/.venv"
WEB_ROOT=/var/www/damf
SERVICE=damf-api
WORKER_SERVICE=damf-worker
NGINX_SITE=damf
DB_NAME=damf
OS_USER="${SUDO_USER:-$(id -un)}"   # systemd 以这个身份跑，也是 socket peer 认证看到的登录用户
DB_USER="$OS_USER"

PORT=""
MOBILE_PORT=""
API_PORT=""
PUB_IP=""
DOMAIN=""
AUTH=""
PROVIDERS=""
SKIP_BUILD=0
SKIP_APT=0
RENDER_ONLY=0
CONFIG_ONLY=0
DRY_RUN=0
RENEW_TLS=0

C_HEAD=$'\e[1;36m'
C_OK=$'\e[1;32m'
C_WARN=$'\e[1;33m'
C_OFF=$'\e[0m'

step() { printf '\n%s▸ %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
already() { printf '  %s∙%s %s（已就绪）\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die() { printf '\n%s中止：%s%s\n' "$C_WARN" "$*" "$C_OFF" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }

priv() { # 特权动作；--dry-run 时只打印
  if [ "$DRY_RUN" = "1" ]; then
    printf '  %s·%s [dry-run] %s\n' "$C_WARN" "$C_OFF" "$*"
  elif [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

must_priv() { # 需要 root 且要看返回码；不能写成 A && B || C，否则 B 失败会被再跑一遍
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

py_pip() { # 往 venv 里装包：优先 pip，没有 pip 就退回 uv（本仓库的 venv 可能正是 uv 灌出来的）
  if ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip "$@"
  elif need uv; then
    VIRTUAL_ENV="$VENV" uv pip "$@"
  else
    die "venv 里既没有 pip 也没有 uv：apt-get install python3-venv python3-pip，或装 uv"
  fi
}

usage() {
  cat <<'EOF'
用法：./install.sh [开关]

  --ip 地址             对外 IP/主机名，写进证书 SAN（默认取 hostname -I 第一个）
  --hostname 域名       额外域名（有域名建议改用 certbot 签发）
  --port 端口           S5 桌面控制台对外端口（默认 8443）
  --mobile-port 端口    S5M 移动控制台对外端口（默认 8444）
  --api-port 端口       factory-api 本机端口，只监听 127.0.0.1（默认 8080）
  --auth local|off      登录模式（默认 local）
  --providers auto|fakes|real 后端能力（默认 auto；fakes 用替身；real 要求 S2/S3/S4 真身已装入）
  --db-user 角色        PostgreSQL 角色（默认当前用户，socket peer 认证，不存口令）
  --skip-build          跳过 npm ci + 前端构建（落地只读 dist/；烘着测试标记的产物会被拒绝）
  --skip-apt            跳过系统包安装
  --renew-tls           强制重签自签证书
  --render-only         只渲染 .deploy/ 下配置，不装依赖、不动系统目录
  --config-only         跳过装依赖/构建/建库，只重渲染并落到 nginx + systemd（改端口/模式时用）
  --dry-run             不写系统目录、不重启服务
  -h, --help            本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ip) PUB_IP="${2:?--ip 需要值}"; shift 2 ;;
    --hostname) DOMAIN="${2:?--hostname 需要值}"; shift 2 ;;
    --port) PORT="${2:?--port 需要值}"; shift 2 ;;
    --mobile-port) MOBILE_PORT="${2:?--mobile-port 需要值}"; shift 2 ;;
    --api-port) API_PORT="${2:?--api-port 需要值}"; shift 2 ;;
    --auth) AUTH="${2:?--auth 需要值}"; shift 2 ;;
    --providers) PROVIDERS="${2:?--providers 需要值}"; shift 2 ;;
    --db-user) DB_USER="${2:?--db-user 需要值}"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --renew-tls) RENEW_TLS=1; shift ;;
    --render-only) RENDER_ONLY=1; shift ;;
    --config-only) CONFIG_ONLY=1; SKIP_APT=1; SKIP_BUILD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "未知开关：$1（--help 看用法）" ;;
  esac
done

RUN_USER="$OS_USER"

# 没显式给的开关，从已生效的配置里读回来 —— 否则裸跑一次 install.sh 就会把上次设定的端口、
# 登录模式和提供方重置成本脚本的默认值。
env_val() { [ -f "$DEPLOY/env" ] && sed -n "s/^$1=//p" "$DEPLOY/env" | tail -1; }
rendered_listen() {
  [ -f "$DEPLOY/nginx.conf" ] || return 0
  grep -oE '^[[:space:]]*listen +[0-9]+ ssl' "$DEPLOY/nginx.conf" | grep -oE '[0-9]+' | sed -n "${1}p"
}
PORT="${PORT:-$(rendered_listen 1)}";           PORT="${PORT:-8443}"
MOBILE_PORT="${MOBILE_PORT:-$(rendered_listen 2)}"; MOBILE_PORT="${MOBILE_PORT:-8444}"
API_PORT="${API_PORT:-$(env_val FACTORY_API_PORT)}"; API_PORT="${API_PORT:-8080}"
# 反代允许的请求体大小。S1 默认收到 100 MB（config.default_max_upload_bytes），nginx 默认只放
# 1 MB；两边不一致时，大文件上传的失败会长在反代上，界面上看不到原因。
MAX_BODY="${MAX_BODY:-$(env_val FACTORY_NGINX_MAX_BODY)}"; MAX_BODY="${MAX_BODY:-100m}"
AUTH="${AUTH:-$(env_val FACTORY_AUTH_MODE)}";   AUTH="${AUTH:-local}"
PROVIDERS="${PROVIDERS:-$(env_val FACTORY_PROVIDER_MODE)}"; PROVIDERS="${PROVIDERS:-auto}"
if [ -z "$PUB_IP" ] && [ -f "$TLS/san.txt" ]; then
  PUB_IP="$(grep -oE 'IP:[0-9.]+' "$TLS/san.txt" | head -1 | cut -d: -f2)"
fi

for p in "$PORT" "$MOBILE_PORT" "$API_PORT"; do
  case "$p" in ('' | *[!0-9]*) die "端口必须是数字" ;; esac
  [ "$p" -ge 1 ] && [ "$p" -le 65534 ] || die "端口 $p 超出范围"
done
[ "$AUTH" = "local" ] || [ "$AUTH" = "off" ] || die "--auth 只能是 local 或 off"
case "$PROVIDERS" in auto | fakes | real) ;; *) die "--providers 只能是 auto、fakes 或 real" ;; esac
[ "$PORT" != "$MOBILE_PORT" ] || die "S5 与 S5M 端口不能相同"
{ [ "$API_PORT" != "$PORT" ] && [ "$API_PORT" != "$MOBILE_PORT" ]; } || die "api 端口不能与对外端口相同"

mkdir -p "$DEPLOY" "$STATE" "$STATE/objects" "$TLS"

# ---------------------------------------------------------------- 前置检查

if [ "$RENDER_ONLY" = "0" ]; then
  step "前置检查"
  [ -r /etc/os-release ] || die "找不到 /etc/os-release，只支持 apt 系发行版"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "$ID ${ID_LIKE:-}" in
    *debian* | *ubuntu*) ok "发行版 ${PRETTY_NAME:-$ID}" ;;
    *) die "不支持的发行版 $ID（脚本按 apt/dpkg 写）" ;;
  esac
  for d in S0 S1 S2 S3 S4 S5 S5M; do
    [ -d "$REPO/$d" ] || die "缺子项目 $d —— 请在仓库根目录运行"
  done
  ok "仓库 $REPO"
  if [ "$(id -u)" -ne 0 ] && ! need sudo; then
    die "需要 root 或 sudo（要写 /etc/nginx、/etc/systemd/system、/var/www）"
  fi

  if [ -z "$PUB_IP" ]; then
    PUB_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$PUB_IP" ] || PUB_IP=127.0.0.1
    warn "未指定 --ip，用本机地址 $PUB_IP 作证书 SAN；公网 IP 不同请带 --ip 重跑"
  fi
  ok "对外 https://$PUB_IP:$PORT（S5） · https://$PUB_IP:$MOBILE_PORT（S5M）"

  # `auto` resolves a real adapter only when the provider package imports, and otherwise
  # silently substitutes the stand-in — which is exactly how a deployment ends up telling
  # operators "nothing compiled from this is deliverable" with nothing in the install log to
  # explain it. So name the resolution here, before the service is written.
  if [ -x "$VENV/bin/python" ]; then
    missing_providers=""
    for pair in "S2:factory_blueprint" "S3:factory_intelligence" "S4:factory_dhis2"; do
      label="${pair%%:*}"; pkg="${pair##*:}"
      "$VENV/bin/python" -c "import ${pkg}.api" 2>/dev/null || missing_providers="${missing_providers} ${label}(${pkg})"
    done
    if [ "$PROVIDERS" = "real" ] && [ -n "$missing_providers" ]; then
      die "--providers real 但真身没装：${missing_providers} —— 先跑 S1/scripts/install_dev.sh"
    fi
    if [ "$PROVIDERS" = "auto" ] && [ -n "$missing_providers" ]; then
      warn "--providers auto：${missing_providers} 缺席，这几路会走替身"
    fi
    [ -n "$missing_providers" ] || ok "S2/S3/S4 真身齐备（--providers ${PROVIDERS}）"
  else
    [ "$PROVIDERS" = "real" ] && die "--providers real 需要 $VENV 先建好（跑 S1/scripts/install_dev.sh）"
    warn "找不到 $VENV/bin/python，跳过提供方检查"
  fi
fi

# ---------------------------------------------------------------- 系统依赖

if [ "$RENDER_ONLY" = "0" ] && [ "$SKIP_APT" = "0" ]; then
  step "系统依赖"
  missing=()
  need git || missing+=(git)
  need curl || missing+=(ca-certificates curl)
  need openssl || missing+=(openssl)
  need python3 || missing+=(python3 python3-venv python3-pip)
  need psql || missing+=(postgresql)
  need nginx || missing+=(nginx)
  if [ ${#missing[@]} -eq 0 ]; then
    already "apt 包齐全"
  else
    warn "待装：${missing[*]}"
    priv apt-get update -qq
    priv env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    ok "已安装"
  fi
fi

if [ "$RENDER_ONLY" = "0" ] && [ "$SKIP_BUILD" = "0" ]; then
  step "Node 运行时（Vite 6 要求 ^20.19 || >=22）"
  node_major="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || echo 0)"
  if [ "${node_major:-0}" -ge 20 ] && need npm; then
    already "node $(node -v) / npm $(npm -v)"
  else
    warn "node 缺失或过旧，装 NodeSource 22"
    priv bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null && apt-get install -y -qq nodejs'
    ok "node $(node -v)"
  fi
fi

# ---------------------------------------------------------------- Python 后端

if [ "$RENDER_ONLY" = "0" ] && [ "$CONFIG_ONLY" = "0" ]; then
  step "Python 环境（S0 + S1；S2/S3/S4 以 --no-deps 装入同一 venv）"
  if [ -x "$VENV/bin/python" ]; then
    already "$VENV"
  else
    python3 -m venv "$VENV"
    ok "创建 $VENV"
  fi
  # 五个包互相以 factory-* 声明依赖，而 S1 钉死 factory-foundation==0.1.0、S0 实际已是 0.1.2，
  # 让解析器去装 factory-* 一定失败。所以：先只装第三方依赖，再把五个本地包 --no-deps 装入。
  mapfile -t third_party < <(REPO="$REPO" "$VENV/bin/python" - <<'PY'
import os, tomllib, pathlib
seen, out = set(), []
for pkg in ("S0", "S1", "S2", "S3", "S4"):
    data = tomllib.loads((pathlib.Path(os.environ["REPO"]) / pkg / "pyproject.toml").read_text())
    for req in data["project"].get("dependencies", []):
        if req.lower().startswith("factory-") or req in seen:
            continue
        seen.add(req)
        out.append(req)
print("\n".join(out))
PY
)
  [ ${#third_party[@]} -gt 0 ] || die "从各 pyproject 里没提取到第三方依赖"
  ok "第三方依赖 ${#third_party[@]} 项（首次安装较慢，之后是空操作）"
  py_pip install -q "${third_party[@]}"
  py_pip install -q --no-deps -e "$REPO/S0" -e "$REPO/S1" -e "$REPO/S2" -e "$REPO/S3" -e "$REPO/S4"
  [ -x "$VENV/bin/factory-api" ] || die "venv 里没有 factory-api"
  "$VENV/bin/python" - <<'PY' || die "后端包导入自检失败"
import importlib
for m in ("factory_foundation", "factory_control", "factory_blueprint", "factory_intelligence", "factory_dhis2"):
    importlib.import_module(m)
print("  ✓ 后端包可导入")
PY
fi

# ---------------------------------------------------------------- 前端构建与落地

if [ "$RENDER_ONLY" = "0" ] && [ "$SKIP_BUILD" = "0" ]; then
  step "前端构建"
  for app in S5 S5M; do
    (
      cd "$REPO/$app"
      if [ -f package-lock.json ]; then npm ci --no-audit --no-fund --silent; else npm install --no-audit --no-fund --silent; fi
      # 生成的类型必须与自己的 vendored 契约一致。构建编译的是**签入**的 src/api/generated，
      # 所以契约改了而生成文件没跟上时 typecheck 照样绿：本轮 S5M 就是这样把 1.10.0 加的
      # codePrefix 漏了过去，重新生成后它反而当场编译不过。备份→重生成→比对→还原，
      # 只判定不改动工作区。
      gen=src/api/generated
      [ -d "$gen" ] || die "$app 没有 $gen —— 先进 $app 跑 npm run codegen"
      backup="$(mktemp -d)"
      cp -a "$gen"/. "$backup"/
      npm run codegen --silent
      # `|| true` 是必需的：pipefail 会让 `diff | head` 以 SIGPIPE 的非零状态退出，而命令替换把
      # 那个状态原样传给赋值 —— 在 set -e 下子 shell 会在这里静默死掉，既不打消息也不还原工作区。
      drift="$(diff -rq "$backup" "$gen" 2>&1 | head -3 | tr '\n' ' ' || true)"
      cp -a "$backup"/. "$gen"/          # 守卫只判定，绝不把重生成留在工作区里
      rm -rf "$backup"
      [ -z "$drift" ] || die "$app/$gen 与 $app/contract/openapi.json 不一致（$drift）—— 进 $app 跑 npm run codegen，把生成文件与契约一起提交"
      npm run build --silent
    )
    [ -f "$REPO/$app/dist/index.html" ] || die "$app 构建后没有 dist/index.html"
    ok "$app → dist/"
  done
fi

if [ "$RENDER_ONLY" = "0" ] && [ "$CONFIG_ONLY" = "0" ]; then
  # nginx 的 worker 是 www-data，而仓库常在 /home/<user>（750，进不去）。构建产物复制到 /var/www
  # 再服务，免得为了读静态文件放宽家目录权限。
  step "静态产物落地 $WEB_ROOT"
  for app in s5 s5m; do
    case "$app" in s5) src="$REPO/S5/dist" ;; *) src="$REPO/S5M/dist" ;; esac
    [ -d "$src" ] || die "没有 $src —— 去掉 --skip-build 先构建"
    # 上线只读 dist/，永远不读测试构建的目录（S5 的 dist-e2e/ 就是替身版）。这里必须拦一道：
    # 替身/夹具是**构建期**烘进 bundle 的，运行期的 config.js 切不掉它，所以拷错目录不会有任何
    # 报错，只会把线上界面变成读 fixture 的控制台 —— 本轮真的部署出来过一次。标记只在入口 chunk 里。
    case "$app" in
      s5) test_marker='VITE_ENABLE_MOCK:"1"' ;;
      s5m) test_marker='dev/json-editor' ;;
    esac
    if grep -qF -- "$test_marker" "$src"/assets/index-*.js 2>/dev/null; then
      die "$app/dist/ 里烘着测试构建的标记（$test_marker），不是可上线的产物。进 $app 跑 npm run build 把生产构建放回 dist/ 再来（e2e 的产物：S5 在 dist-e2e/，S5M 也在 dist-e2e/，都不碰 dist/，见 user-guide §11 坑 18）"
    fi
    # 整目录换掉，不是往旧目录里叠加。chunk 文件名带内容哈希，每次构建都换名字，所以旧文件不会被
    # 新 index.html 引用 —— 但它们会一直堆在那里（本机曾堆到 592 个），而缓存里还留着旧 index.html
    # 的浏览器会继续拉到旧 bundle：症状正是"我看到的界面跟这次部署的不一样"，最难查的那一类。
    priv mkdir -p "$WEB_ROOT"
    priv rm -rf "$WEB_ROOT/$app.next"
    priv mkdir -p "$WEB_ROOT/$app.next"
    priv cp -a "$src/." "$WEB_ROOT/$app.next/"
    priv rm -rf "$WEB_ROOT/$app.prev"
    if [ -d "$WEB_ROOT/$app" ]; then priv mv "$WEB_ROOT/$app" "$WEB_ROOT/$app.prev"; fi
    priv mv "$WEB_ROOT/$app.next" "$WEB_ROOT/$app"
    priv rm -rf "$WEB_ROOT/$app.prev"
    priv chmod -R a+rX "$WEB_ROOT/$app"
    ok "$src → $WEB_ROOT/$app"
  done
fi

# ---------------------------------------------------------------- 数据库

if [ "$RENDER_ONLY" = "0" ] && [ "$CONFIG_ONLY" = "0" ]; then
  step "PostgreSQL"
  priv systemctl enable --now postgresql
  as_pg() { must_priv su postgres -c "psql -tAc \"$1\""; }
  if [ "$(as_pg "select 1 from pg_roles where rolname='${DB_USER}'" || true)" = "1" ]; then
    already "角色 $DB_USER"
  else
    as_pg "CREATE ROLE ${DB_USER} LOGIN CREATEDB" >/dev/null
    ok "角色 $DB_USER"
  fi
  if [ "$(as_pg "select 1 from pg_database where datname='${DB_NAME}'" || true)" = "1" ]; then
    already "库 $DB_NAME"
  else
    as_pg "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}" >/dev/null
    ok "库 $DB_NAME"
  fi

  # Ubuntu 默认 local 走 peer：OS 登录用户与角色同名即可连，不必把 DB 口令写进配置。
  [ "$DB_USER" = "$RUN_USER" ] || warn "PG 角色 $DB_USER 与登录用户 $RUN_USER 不同，peer 认证会失败——需改用口令连接"
  # factory-api 以 systemd 的 User 身份连，这里也按同一身份自检。
  if must_priv su - "$RUN_USER" -c "psql 'postgresql://${DB_USER}@/${DB_NAME}?host=/var/run/postgresql' -tAc 'select 1'" >/dev/null 2>&1; then
    ok "以 $RUN_USER 的 socket peer 认证可连"
  else
    die "以系统用户 $RUN_USER 连 ${DB_USER}/${DB_NAME} 失败。请让 pg_hba 对 local 用 peer 且角色名与登录用户一致（--db-user $(id -un)），或改用口令：先 ALTER ROLE ${DB_USER} PASSWORD '…'，再把 .deploy/env 里 DATABASE_URL 写成 postgresql://${DB_USER}:口令@/${DB_NAME}?host=/var/run/postgresql"
  fi
  # 五个 schema（core/dx/bp/ai/cp）由 factory-api 启动时的 alembic 建，这里不预建。
fi

# ---------------------------------------------------------------- 配置渲染

env_file="$DEPLOY/env"

env_set() { # 就地更新（含被注释掉的同名行），其余内容与手工注释保持不动。
  # 值没变就绝不碰文件——start.sh 用 env 文件的 mtime 判断"配置是否已生效"。
  local key="$1" value="$2"
  [ "$(sed -n "s/^${key}=//p" "$env_file" 2>/dev/null | tail -1)" = "$value" ] && return 0
  touch "$env_file"
  if grep -qE "^[#]?${key}=" "$env_file"; then
    sed -i -E "s|^[#]?${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

env_set_once() { # 只在这项还没有生效值时写入；已有值绝不改动。
  # 主密钥重写一次，就会让库里每一个已存凭证解不开（keyMismatch）——那是要人重新录入每一个
  # 环境、每一个模型 key 的代价，绝不能由一次例行 ./install.sh 触发。
  local key="$1" value="$2"
  grep -qE "^${key}=" "$env_file" && return 0
  env_set "$key" "$value"
}

step "运行时配置 .deploy/env"
if [ ! -s "$env_file" ]; then
  gen_pw="Damf-$(openssl rand -hex 6)"
  gen_key="$(openssl rand -hex 32)"
  cat >"$env_file" <<EOF
# DAMF 运行时配置（systemd EnvironmentFile）。含口令，权限 0600，不要外传。
# 改完 ./start.sh --restart 生效。

DATABASE_URL=postgresql://${DB_USER}@/${DB_NAME}?host=/var/run/postgresql
# 上传件与产物落本地磁盘，重启不丢（S0 支持 file:// / memory:// / s3://）。
OBJECT_STORE_URL=file://${STATE}/objects
# 提醒：SECRET_BACKEND 目前只被读取、不被任何代码使用。实际注册的后端是 env + memory，
# 再配上 FACTORY_CREDENTIAL_MASTER_KEY 时多一个只读的 db（密文在 cp.secrets）。
SECRET_BACKEND=env

FACTORY_API_HOST=127.0.0.1
FACTORY_API_PORT=${API_PORT}
FACTORY_MIGRATIONS_MODE=apply
FACTORY_WORKFLOW_ENGINE=inprocess
FACTORY_PROVIDER_MODE=${PROVIDERS}
FACTORY_FAKE_ENVIRONMENTS=dev:TEST,prod:PRODUCTION

# 会话 Cookie 是 Secure 的，登录模式必须配 HTTPS（nginx 已提供）。
FACTORY_AUTH_MODE=${AUTH}
FACTORY_HTTPS_ONLY=true
FACTORY_SESSION_IDLE_MINUTES=30
FACTORY_SESSION_ABSOLUTE_HOURS=12
FACTORY_BOOTSTRAP_ADMIN_SUBJECT=admin
FACTORY_BOOTSTRAP_ADMIN_SECRET_REF=env://FACTORY_BOOTSTRAP_ADMIN_PASSWORD
FACTORY_BOOTSTRAP_ADMIN_PASSWORD=${gen_pw}

# 真实模型与 DHIS2 凭据有两条录入路径，任选其一（也可以混用）：
#   A. 界面录入（推荐）：在"开始使用"或环境/治理页直接填用户名/口令、API key。值经 HTTPS 发一次，
#      用下面的主密钥加密后存进 cp.secrets，界面之后只看到 db://名字。需要先有主密钥。
#   B. 环境变量：把值填在这里，界面里用 env://名字 引用（不依赖主密钥，但要改文件并重启）。
#      治理 → 提供方：apiKeySecretRef=env://MODEL_API_KEY
#      环境 → 凭据：  secretRef=env://DHIS2_DEV_TOKEN（PRODUCTION 需 discovery + import 两个）
# MODEL_API_KEY=
# MODEL_BASE_URL=
# DHIS2_DEV_TOKEN=
# DHIS2_PROD_DISCOVERY_TOKEN=
# DHIS2_PROD_IMPORT_TOKEN=

# 界面录入凭证的主密钥（FR-SEC-006、CP-SEC-001）：不入库、不回显、只写在这里（本文件 0600）。
# 删掉这一行 = 关掉界面录入：相关端点回 503，其余功能照常，服务仍能启动。
# 改掉这一行的值 = 库里每一个已存凭证都解不开（报 keyMismatch），必须逐个重新录入。
FACTORY_CREDENTIAL_MASTER_KEY=${gen_key}
EOF
  ok "生成（管理员口令见最后）"
else
  already "已存在，只同步端口与模式"
  # 老机器升级：补一个主密钥，界面录入才可用；已有则原样保留（见 env_set_once）。
  env_set_once FACTORY_CREDENTIAL_MASTER_KEY "$(openssl rand -hex 32)"
fi
chmod 600 "$env_file"
env_set FACTORY_API_PORT "$API_PORT"
env_set FACTORY_PROVIDER_MODE "$PROVIDERS"
env_set FACTORY_AUTH_MODE "$AUTH"
env_set FACTORY_HTTPS_ONLY true

step "TLS 证书"
san="IP:${PUB_IP},IP:127.0.0.1,DNS:localhost"
[ -n "$DOMAIN" ] && san="${san},DNS:${DOMAIN}"
host_alias="$(hostname 2>/dev/null || true)"
[ -n "$host_alias" ] && san="${san},DNS:${host_alias}"
if [ "$RENEW_TLS" = "1" ] || [ ! -s "$TLS/cert.pem" ] || [ ! -s "$TLS/key.pem" ] || [ "$(cat "$TLS/san.txt" 2>/dev/null || true)" != "$san" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$TLS/key.pem" -out "$TLS/cert.pem" \
    -days 825 -subj "/CN=DAMF ${PUB_IP}" -addext "subjectAltName=${san}" \
    -addext "basicConstraints=critical,CA:TRUE" 2>/dev/null
  printf '%s\n' "$san" >"$TLS/san.txt"
  chmod 600 "$TLS/key.pem"
  ok "自签证书 SAN=${san}"
else
  already "证书有效且 SAN 未变（要强制重签用 --renew-tls）"
fi

api_block() { # 两个站点共用的 /api/v1 与 SSE 段；\$ 是给外层 heredoc 的，输出后即成普通 $
  cat <<EOF
    location ~ ^/api/v1/workflows/.+/events\$ {
        proxy_pass         http://damf_api;
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_set_header   Host \$host;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_buffering    off;
        proxy_cache        off;
        chunked_transfer_encoding off;
        gzip               off;
        proxy_read_timeout 1h;
        add_header X-Accel-Buffering no always;
    }

    location /api/v1/ {
        proxy_pass         http://damf_api;
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 1h;
        # 需求文档走这条路上传。S1 的上限是 config.default_max_upload_bytes（默认 100 MB），
        # nginx 自己的默认是 1 MB —— 不写这一条，真实的 Word/PDF 材料在还没见到 S1 之前就被 413
        # 拦下，而界面上只剩一句"上传失败"。要调就改 MAX_BODY，别用 0（那是取消限制）。
        client_max_body_size ${MAX_BODY};
    }
EOF
}

site_block() { # $1=对外端口 $2=web 子目录
  cat <<EOF
server {
    listen $1 ssl;
    listen [::]:$1 ssl;
    server_name ${PUB_IP}${DOMAIN:+ ${DOMAIN}};

    ssl_certificate     ${TLS}/cert.pem;
    ssl_certificate_key ${TLS}/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 497 是 nginx 内部码「明文打到了 TLS 端口」；不接住它就是一屏 400。
    error_page 497 =301 https://\$host:\$server_port\$request_uri;

    root ${WEB_ROOT}/$2;
    index index.html;

    # config.js 按环境注入，绝不缓存；带哈希的产物缓存一年。
    location = /config.js { add_header Cache-Control "no-cache, no-store, must-revalidate"; }
    location /assets/      { add_header Cache-Control "public, max-age=31536000, immutable"; }
    location / { try_files \$uri \$uri/ /index.html; }

$(api_block)
}
EOF
}

step "nginx 站点"
{
  echo "# 由 install.sh 渲染，勿手工编辑（改配置请重跑 ./install.sh --render-only）。"
  echo "# 同源约定：静态站与 /api/v1 同一 origin（UI-APP-008 / UM-COMP-004）——S1 没有 CORS 中间件。"
  echo
  echo "upstream damf_api {"
  echo "    server 127.0.0.1:${API_PORT};"
  echo "    keepalive 16;"
  echo "}"
  echo
  site_block "$PORT" s5
  echo
  site_block "$MOBILE_PORT" s5m
} >"$DEPLOY/nginx.conf"
ok "渲染 .deploy/nginx.conf（${PORT} / ${MOBILE_PORT} → 127.0.0.1:${API_PORT}）"

step "systemd 单元 $SERVICE"
cat >"$DEPLOY/$SERVICE.service" <<EOF
# 由 install.sh 渲染。端口等全部来自 EnvironmentFile，改 .deploy/env 后 restart 即可。
[Unit]
Description=DAMF factory-api (S1 control plane)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO}/S1
EnvironmentFile=${env_file}
ExecStart=${VENV}/bin/factory-api
Restart=always
RestartSec=3
TimeoutStartSec=180
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
ok "渲染 .deploy/$SERVICE.service"

step "systemd 单元 $WORKER_SERVICE"
cat >"$DEPLOY/$WORKER_SERVICE.service" <<EOF
# 由 install.sh 渲染。与 factory-api 同一个 EnvironmentFile；引擎语义由
# FACTORY_WORKFLOW_ENGINE 决定：inprocess 时这个进程承载八个周期任务（审计链、事件清理、
# 投递重试、Gate 催办……），temporal 时它是 Temporal worker。它不开端口，nginx 与它无关。
[Unit]
Description=DAMF factory-worker (scheduled jobs / Temporal worker)
After=network-online.target postgresql.service ${SERVICE}.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO}/S1
EnvironmentFile=${env_file}
ExecStart=${VENV}/bin/factory-worker
Restart=always
RestartSec=5
TimeoutStartSec=180
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
ok "渲染 .deploy/$WORKER_SERVICE.service"

if [ "$RENDER_ONLY" = "1" ]; then
  printf '\n%s只渲染完成%s（未动系统目录）\n' "$C_OK" "$C_OFF"
  exit 0
fi

# ---------------------------------------------------------------- 落到系统

step "写入系统配置"
priv install -m 0644 "$DEPLOY/nginx.conf" "/etc/nginx/sites-available/${NGINX_SITE}.conf"
priv ln -sfn "/etc/nginx/sites-available/${NGINX_SITE}.conf" "/etc/nginx/sites-enabled/${NGINX_SITE}.conf"
if [ "$DRY_RUN" = "1" ]; then
  warn "dry-run：跳过 nginx 校验与重载"
else
  if must_priv nginx -t >/dev/null 2>&1; then
    must_priv systemctl reload nginx
    ok "nginx 已重载"
  else
    must_priv rm -f "/etc/nginx/sites-enabled/${NGINX_SITE}.conf"
    warn "nginx 校验失败，已撤销本站点，现有站点未受影响："
    must_priv nginx -t 2>&1 | sed 's/^/    /' >&2 || true
    die "nginx -t 未通过"
  fi
fi
priv install -m 0644 "$DEPLOY/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
priv install -m 0644 "$DEPLOY/$WORKER_SERVICE.service" "/etc/systemd/system/$WORKER_SERVICE.service"
priv systemctl daemon-reload
priv systemctl enable "$SERVICE" >/dev/null 2>&1 || true
priv systemctl enable "$WORKER_SERVICE" >/dev/null 2>&1 || true
ok "$SERVICE 与 $WORKER_SERVICE 已注册（尚未启动，交给 start.sh）"

# ---------------------------------------------------------------- 收尾

ADMIN_SUBJECT="$(sed -n 's/^FACTORY_BOOTSTRAP_ADMIN_SUBJECT=//p' "$env_file" | tail -1)"
ADMIN_PW="$(sed -n 's/^FACTORY_BOOTSTRAP_ADMIN_PASSWORD=//p' "$env_file" | tail -1)"

cat <<EOF

${C_OK}安装完成${C_OFF}
  启动 ./start.sh          停止 ./start.sh --stop      状态 ./start.sh --status      日志 ./start.sh --logs
  改端口 ./start.sh --port 9443      改模式 ./start.sh --auth off --providers fakes

  外网访问（自签证书，浏览器会告警，点「继续访问」）：
    S5  桌面控制台   https://${PUB_IP}:${PORT}/
    S5M 移动控制台   https://${PUB_IP}:${MOBILE_PORT}/
    API 只在 127.0.0.1:${API_PORT}，由同源反代暴露为 /api/v1

  登录（FACTORY_AUTH_MODE=${AUTH}）：
    账号 ${ADMIN_SUBJECT}   口令 ${ADMIN_PW}
    口令在 $env_file（0600）。其余账号在 S5 的 Users 页 / S5M 的「权限管理」里建。

  还需要你做的：
    1. 打开 ${C_HEAD}https://${PUB_IP}:${PORT}/getting-started${C_OFF}（手机：/start）——
       这台机器现在是「能跑但什么都没配」。那一页一步一屏：选厂商、录 key、登记模型、
       登记 DHIS2 实例（含用户名/口令）、建项目，全程不用离开浏览器，也不用再 SSH 改文件；
       它同时把 ${env_file} 里写错的变量名原样列出（不列取值）；
    2. 云安全组或防火墙放行 ${PORT} 与 ${MOBILE_PORT}；
    3. 把引导口令换成自己的（改 $env_file 后 ./start.sh --restart）；
    4. 真实模型/DHIS2：在界面里直接录入即可（值经主密钥加密存进 cp.secrets，之后只见 db://名字）。
       更愿意把值留在环境变量里也行——填进 $env_file，界面里用 env://名字 引用；
       两种可以混用。删掉 FACTORY_CREDENTIAL_MASTER_KEY 即关掉界面录入，那时只剩 env:// 一条路；
    5. 有域名时改用 certbot 证书，替换 $TLS 下的 key/cert 后 ./start.sh --restart；
    6. APP_ENV=production 已可用：bootstrap 在生产档只挂 memory 后端，进程照常启动；
       代价是 env:// 引用按 FD-SEC-001 的设计解析失败，凭证走 db:// 主密钥或部署侧注入。
       本脚本仍默认不设 APP_ENV（保持开发档行为）。凭证库的注册与这一行无关，
       它只看 FACTORY_CREDENTIAL_MASTER_KEY 在不在。
EOF
