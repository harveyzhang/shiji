# 拾迹（shiji）调查引擎

上传一张老照片或一件旧物，后端做这几步：拒查 → 识图（可选）→ 三次目的不同的元宝搜索（identify / verify / context）→ 整理来源 → 给出判断 → 生成微型场景配置。前端按返回的 JSON 渲染四屏和 3D 场景。

产品范围、契约补丁和分工在管理入口：`20_Projects/个人/探索与实践/1024 超能 Coding 周末/拾迹/` 下的《拾迹_MVP功能冻结_V1.0_20261024》和《拾迹_开工清单与契约补丁》。

## 运行

```bash
uv sync
SHIJI_SEARCH_DISABLED=1 uv run shiji        # 不调用搜索，只看接口和页面联调
uv run shiji                                 # 真实搜索，需要下面的环境变量；默认 127.0.0.1:8025
```

| 变量 | 作用 |
|---|---|
| `SHIJI_WSA_KEY_FILE` | 元宝搜索 key 文件，内容为一行 `TENCENTCLOUD_WSA_APIKEY=…` |
| `SHIJI_LEDGER` | 额度账本文件；和团队其他工具共用同一个，才能算对总用量 |
| `SHIJI_CACHE_DIR` | 搜索缓存目录；同一查询 24 小时内不重复扣额度 |
| `SHIJI_SEARCH_DISABLED` | 设为 `1` 时不搜索，调查结果一律是 `search_failed` |
| `SHIJI_MINIMAX_KEY_FILE` | MiniMax Token Plan key 文件，内容为一行 `MINIMAX_API_KEY=…`（国内区，走 `api.minimax.cn`）；配了就先用它识图（Token Plan 看图接口）和归纳（`MiniMax-M3.1-Flash`），失败再退到 Toklens |
| `SHIJI_MINIMAX_MODEL` | MiniMax 归纳模型，默认 `MiniMax-M3.1-Flash` |
| `SHIJI_VISION_KEY_FILE` | Toklens key 文件，内容为一行 `TOKLENS_API_KEY=…`；设了就同时打开识图和归纳，不设就只按文字线索调查、按规则写判断 |
| `SHIJI_VISION_MODEL` | 识图模型，默认 `gpt-5.6-luna`（实测认字比 `gpt-6-luna` 准，单张约 5–15 秒） |
| `SHIJI_WRITER_MODEL` | 归纳模型，默认同样是 `gpt-5.6-luna` |
| `SHIJI_VISION_BASE_URL` | 识图接口地址，默认 `https://lax.toklens.ai/v1` |
| `SHIJI_HOST` / `SHIJI_PORT` | 监听地址，默认 `127.0.0.1` / `8025` |
| `SHIJI_CORS_ORIGINS` | 允许跨域的前端地址，逗号分隔；默认 `http://localhost:5173` |

## 接口

`GET /api/health` 返回 `{"ok": true, "search": true, "vision": false, "writer": false}`，表示搜索、识图、归纳各自有没有打开。

`POST /api/investigate`，`multipart/form-data`：

| 字段 | 说明 |
|---|---|
| `inputType` | `old_photo` 或 `object`，必填 |
| `userHint` | 用户补充的文字线索，可选 |
| `image` | JPG、PNG 或 WebP，最大 8 MB，可选；只在内存里使用，不落盘 |

```bash
curl -s -F inputType=object -F userHint=煤油灯 http://127.0.0.1:8025/api/investigate
```

返回契约 JSON，字段和白名单以 `src/shiji/contract.py` 为准。`status` 的取值：

- `ok`：识图成功，找到了来源
- `text_only`：没有图片，或识图没接上、识图失败，按文字线索调查
- `insufficient`：查不到直接记录，场景退回中性调查台
- `search_failed`：搜索全部失败或已关闭，不下结论
- `refused`：问人物身份或时政，直接拒绝

出错时的状态码：422 表示 `inputType` 不对；415 表示上传的不是图片；413 表示图片太大；429 表示同一访问者一小时内超过 30 次。

## 给前端

`mock/mock-object-lamp.json` 和 `mock/mock-photo-station.json` 是开发模拟数据，来源链接都指向 `example.invalid`，只用来搭页面和 3D，不能当真实结果提交。两份数据的场景类型、组件组合和布局都不同。契约变了就重新生成：

```bash
uv run python scripts/make_mocks.py
```

## 边界

- 每次搜索先记账再发送；账本满了就停，不会多扣额度。
- 问人物身份、时政的线索直接拒绝；来源里出现时政内容的，不进报告。
- 判断先按规则生成；打开归纳后，由模型只根据这次搜到的来源改写措辞、挑选引用。强弱由代码判定，不由模型决定：
  - 引用里有 2 个以上不同发布方，才标「有证据支持」或「说法不一」；只有 1 个发布方的标「推断」。维基镜像、同一网站的子域名都只算一个发布方。
  - 模型可以改写或加强一条判断，不能削弱它。
  - 引用了不存在的来源、「它是什么」引用的来源没提到搜索词、或者写了时政内容，这一条就退回规则版。
  - 模型调用失败时整体退回规则版，并在 `warnings` 里写明原因。
- 场景物件只从 12 种白名单组件里选，位置、比例、角度都会被限制在舞台范围内；标「有证据支持」却没有真实来源的物件，会自动降为「推断」。

## 页面

浏览器打开服务根路径 `/` 就是完整的产品页，四屏在同一个页面里切换：

- **首页**：一句话介绍、三层溯源说明、老照片和旧物两个入口、两张示例图（维基共享资源，公有领域和 CC0，来源见 `src/shiji/static/samples/SOURCES.md`）。选正阳门东车站那张时，会把原图注「正阳门东车站」填进线索并明说；删掉就只靠图片去查。
- **调查中**：读 `POST /api/investigate/stream` 的实时事件，逐步显示识图结果、三次搜索的查询词和来源数、归纳和搭建现场。
- **报告**：按来历、可信度、场景三层组织。来源卡片标出类型（政府、档案方志、媒体、百科、其他）和被哪条结论引用。没查清是什么时，年代和当年环境都不下结论，并引导「补充线索，再查一次」（同一张图，按新线索重查）。
- **历史现场**：Three.js r186（MIT，放在 `src/shiji/static/vendor/three/`，不依赖外网 CDN），有阴影、材质、证据色盘和浮动标签，点带圈的物件看依据。场景太空旷时补上标明「体验补全」的常见物件。
- **分享与档案**：每次调查存成 `/c/<id>`（只存结果，不存图片），可以复制链接；本机浏览器记住最近 12 次调查。

## 部署

线上地址 https://sj.wan7426.top 。自己部署时：

- 用 `deploy/Dockerfile` 构建镜像。服务只监听 `127.0.0.1:8025`，前面放一个反向代理，示例见 `deploy/nginx.example.conf`。
- key 放在服务器上单独的目录（目录 700、文件 600），只读挂载进容器，用上面的环境变量指向它们。
- 额度上限用 `SHIJI_MONTH_CAP`、`SHIJI_DAY_CAP` 设置；订单目录用 `SHIJI_ORDER_DIR`。

更新代码时，先提交，再运行：

```bash
deploy/deploy.sh <ssh-host-alias> [月上限] [日上限]   # 服务器目录默认 /opt/shiji，可用 SHIJI_REMOTE_DIR 改
```

脚本只打包已提交的代码，不复制也不打印 key；服务器上缺少 key 文件时会直接停止。

## 测试

```bash
uv run python -m unittest discover -s tests -t .
```
