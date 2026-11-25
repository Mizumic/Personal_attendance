# 宿舍出勤统计（dorm-attendance）

纯前端 PWA，一个 `index.html` 加一个 service worker，装到手机主屏用。
宿舍四个人每天的出勤、作业、请假都在这一页上记，数据留在本机 `localStorage`，
需要推送时由「云端提醒」把内容发给服务器上的 `attendance-server`。

- 线上：<https://mizumic.github.io/Personal_attendance/>
- 仓库：`git@github.com:Mizumic/Personal_attendance.git`（**公开仓库**，注释和示例只用通用课名，别写真实数据和真名）
- 相关：`../attendance-server/`（接收端）、`.workbuddy/memory/notes/dorm-attendance-约束明细.md`（完整约束）

## 改代码之前

1）先读 `../.workbuddy/memory/notes/dorm-attendance-约束明细.md`。那份写了为什么这么建模：
统一状态模型、调休日的课表周次、备注按签名共享、作业"完成=归档"、字段纯增量。
**看起来多余的设计多半是踩过坑的**，别顺手简化。

2）提交前必须过门禁：

```bash
python check.py            # 六项：隐私 / 引用 / PWA / 处理器 / 语法 / 泄漏
python check.py --install-hook   # 装成 pre-commit，之后每次提交自动跑
```

3）改了 `index.html` 就要一起动缓存版本，否则手机上拿到的还是旧的：

- `sw.js` 的 `CACHE` 加一（现在是 `dorm-attendance-v14`）
- `index.html` 里的 `APP_VER` 同步

4）推送走 SSH：`GIT_SSH_COMMAND=ssh git push origin main`（本机代理对 git over https 不友好）。

## 数据怎么流

```
手机（localStorage）──「云端提醒」表单──> https://mizumic.me/api/homework
                                              └─ /srv/attendance 收下，按 Bark 推给订阅者
```

浏览器是 https 页面，只能往 https 接口发；本机没有后端，也不打算加。

## 云端提醒能做什么（2026-11-26）

- **多个推送时点**：`cloudCfg.pushTimes`（数组，最多 8 个）随每次同步发给服务器；
  每个时点当天各推一次。老的单值 `pushTime` 仍然保留并一起发，旧服务器读它会退化成"只用第一个时点"。
- **单条作业的一次性提醒**：作业卡片上的「⏰ 提醒」→ 1 小时后 / 5 小时后 / 1 天后 / 自定义时间。
  存在作业对象的 `remindAt`（`YYYY-MM-DD HH:MM`）上，随快照一起同步；到点由服务器单独推那一条。
  标成完成后 `remindAt` 会被清掉（否则「恢复」后又会冒出几天前的旧提醒）。
- **全部用自绘控件**：时点和自定义提醒都是 ± 按钮步进器，不用原生 `time` / `datetime-local` ——
  iOS 主屏模式下原生弹层可能"看不见但能盲点"（同作业表单里的日期选择器）。

对应的服务器实现见 `../attendance-server/push_check.py`（`sub_times` / `due_reminders`）。

## 真机验证

`_test/verify.py` 起本地服务跑断言，`_test/shots.py` 用无头 Chrome 截图。
改了界面或 PWA 行为，这两步都要跑，光看桌面浏览器不算数。
2026-11-26 起另有 `_test/verify_remind.py` 专门验多时点与提醒面板（17 项）。
