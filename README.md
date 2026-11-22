# ai-crypto-daily：AI × 币圈每日 X 素材包

每天早上自动抓一遍币圈和 AI 圈的数据，再由 Claude Code 写成当天能直接发到 X 的素材：

- **早报长推**：BTC / ETH / ZEC 行情、恐慌贪婪指数、资金费率，加上 5–8 条要闻，每条都附来源链接
- **快评选题**：从你关注的博主推文里挑出讨论度最高的几条，附上起草好的引用点评
- **录屏视频选题**：GitHub 上新出的 AI × 交易 / 量化 / 链上开源项目，附录屏分镜和开头文案
- **去 AI 味**：成稿自动过一遍 [lieflat-less-ai-tone](https://github.com/larashero3-dotcom/lieflat-less-ai-tone) 检测

示例输出见 [examples/](./examples/)。

## 工作方式

```
每天 7:50  collect.py 自动采集（纯 Python 标准库，不调用 AI，不需要任何 API Key）
             ↓
        output_dir/data/<日期>_原始数据.md
             ↓
你在 Claude Code 里说「出今天的素材包」
             ↓
        output_dir/<日期>_素材包.md   ← 你改一改、补上自己的观点，手动发布
```

采集和写作分开：采集不花钱，写作用你已有的 Claude Code。程序不自动发帖，因为 X 的原创奖励看重真人原创，自动发帖的号也容易被判成机器人。

## 数据源

| 类别 | 来源 |
|---|---|
| 行情 | CoinGecko（价格、涨跌、成交额），OKX 永续合约（资金费率、持仓量），alternative.me（恐慌贪婪指数） |
| 币圈新闻 | CoinDesk、Cointelegraph、The Block、Decrypt、Odaily 快讯 |
| AI 新闻 | The Verge AI、量子位、OpenAI News、Google AI、Simon Willison |
| X 博主 | 通过 [fxtwitter](https://github.com/FixTweet/FxTwitter) 公开接口读取，无需登录 |
| GitHub | GitHub 搜索接口，找近 30 天新建、星数在涨的项目 |

全部可以在 `config.toml` 里增删。

## 安装

需要 Python 3.11 以上（用到标准库的 `tomllib`）和 [Claude Code](https://claude.com/claude-code)。

```bash
# 1. 下载本项目
cd ~/Documents
git clone https://github.com/jiajanpp520-spec/ai-crypto-daily.git

# 2. 试跑一次采集
python3 ai-crypto-daily/collect.py

# 3. 安装 Claude Code Skill
mkdir -p ~/.claude/skills
cp -R ai-crypto-daily/skills/x-daily-pack ~/.claude/skills/
# 打开 ~/.claude/skills/x-daily-pack/SKILL.md，把 <项目目录> 换成实际路径，
# 把 <output_dir> 换成 config.toml 里的 output_dir

# 4.（可选）安装去 AI 味 Skill
npx skills add larashero3-dotcom/lieflat-less-ai-tone
```

### 每天自动采集（macOS）

新建 `~/Library/LaunchAgents/com.example.ai-crypto-daily.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.example.ai-crypto-daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>/Users/你的用户名/Documents/ai-crypto-daily/collect.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>50</integer></dict>
</dict>
</plist>
```

把 Python 路径和项目路径换成你自己的，然后运行：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.ai-crypto-daily.plist
```

电脑睡眠时错过的任务，醒来后会补跑。Linux 用 cron 即可。

## 配置

`config.toml` 里可以改：

- `coins`：关注的币种（CoinGecko id + OKX 永续合约名）
- `feeds`：RSS 新闻源
- `kols`：要盯的 X 博主，按组分
- `github.queries`：GitHub 搜索关键词
- `lookback_hours`、`output_dir`

账号定位、人设和写作规则在 `skills/x-daily-pack/SKILL.md` 里，按你自己的账号改。

## 注意

- 素材包只做信息整理，**不构成投资建议**。Skill 的写作规则禁止荐币、喊单和编造经历。
- 录屏演示交易类项目时请用模拟盘或测试网。
- fxtwitter 和 GitHub 的未登录接口偶尔会限流或返回空结果，采集程序会重试，失败的源会写在原始数据末尾。

## 许可

MIT
