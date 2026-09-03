# 01.AI Interactive Website 首页规格

状态：已确认，供首页设计与实现使用

范围：仅新首页，目标设备为 1920px 桌面端。二级产品页、移动端布局和旧页面清理不属于本规格。

## Problem Statement

现有网站拥有丰富且大多有价值的公司文案、产品页、行业案例和可操作 Demo，但它们同时承担公司介绍、产品目录、技术架构、案例证明和运行时入口，访问者难以在短时间内理解 01.AI 的共同方法。

当前需要一套新的首页，把分散资产重新组织成一条可理解、可操作、可验证的主叙事，同时保留多个 Demo 入口而不让它们变成彼此竞争的主线。

## Solution

建立一个面向外部访问者的 01.AI 旗舰入口。首页先通过创始人和公司身份建立可信度，再用一句核心主张说明价值，随后用统一的五步运营闭环解释方法，最后用 Leadership AI 和 Mining 两个主证明展示数字决策与物理运营如何落地。

首页统一使用以下主张和步骤语言：

> 01.AI helps institutions turn consequential decisions and physical operations into accountable AI-native systems.

`Signal → Context → Decision → Action → Outcome`

其余能力和 Demo 作为可选择的深入入口：WorldWise 与 FDE 共同解释从 Agent 能力到生产结果的交付链；Ontology、Solar、Refinery 和 Kazakhstan Energy 进入分组后的 Demo Gallery；Boss AI、Investor AI 和 TopSales AI 进入二级产品入口。

## User Stories

1. 作为首次访问的企业或政府决策者，我希望在进入页面后先知道 01.AI 是谁以及由谁领导，从而判断这家公司是否值得继续了解。
2. 作为首次访问者，我希望在创始人和公司介绍之后立即看到一句清楚的核心主张，从而知道 01.AI 解决的是哪类问题。
3. 作为 CEO 或高级管理者，我希望看到重要决策和物理运营被放在同一套运营逻辑中，从而理解这不是单点模型或普通聊天工具。
4. 作为技术、数据或安全负责人，我希望看到 Ontology、动态上下文、Agent、策略和人工权限在同一结构中的位置，从而判断系统是否具备企业落地条件。
5. 作为政府或行业负责人，我希望看到数字决策和复杂物理运营都能使用同一条闭环，从而评估它是否适用于本机构或行业。
6. 作为潜在合作伙伴，我希望看到策略、产品、行业运营和 FDE 如何连接，从而理解 01.AI 的交付边界和合作方式。
7. 作为任何首页访问者，我希望通过滚动依次看到 Signal、Context、Decision、Action 和 Outcome，从而建立一条连贯的心理模型。
8. 作为不熟悉 AI 术语的访问者，我希望每个术语都绑定到一个真实的经营对象、关系或行动，从而不需要先学习抽象架构。
9. 作为熟悉系统的访问者，我希望通过节点点击跳到闭环中的任意步骤，从而不必重复观看完整动画。
10. 作为首次访问者，我希望核心运营闭环默认按滚动推进，从而获得明确的观看顺序。
11. 作为访问者，我希望核心闭环使用强沉浸视觉表现机构现实、上下文、决策和执行之间的关系，从而直观看见系统如何改变状态。
12. 作为访问者，我希望每个主要动画都对应事实、关系、判断、行动或结果变化，从而不会把视觉效果误认为产品能力。
13. 作为 Leadership AI 的潜在使用者，我希望在首页直接操作工作台，而不是只看截图或视频，从而判断产品是否真的支持管理工作。
14. 作为第一次操作 Leadership AI 的用户，我希望页面提供 `Daily Vanguard → Risk Alert → Evidence → Execution` 的推荐路径，从而快速理解产品工作流。
15. 作为熟悉 Leadership AI 的用户，我希望推荐路径不会限制我的自由操作，从而可以直接访问会议、战略分析或其他工作区。
16. 作为 Leadership AI 用户，我希望工作台在加载失败时显示明确状态并提供原地重试，从而知道问题是加载中还是产品不可用。
17. 作为矿业或资源行业负责人，我希望看到 mine–rail–port 的完整运营链，而不是一个孤立模型，从而判断系统是否理解跨环节约束。
18. 作为 Mining 主证明的访问者，我希望右侧章节滚动时左侧系统视觉同步高亮主数据、排程、运营智能和结果，从而把文字和系统状态对应起来。
19. 作为 Mining 主证明的访问者，我希望可以点击六步决策循环中的节点，从而直接查看 Facts、Agents、Tools、Options、Approve 和 Feedback 的含义。
20. 作为 Mining 行业访问者，我希望能从首页进入现有的综合运营深页，从而继续查看主数据、动态排程和运营智能的完整说明。
21. 作为访问者，我希望 Leadership AI 和 Mining 获得最大的视觉权重，从而知道哪两个场景是首页的主要证明。
22. 作为访问者，我希望看到 WorldWise 和 FDE 被解释为同一条生产交付链，从而理解 Agent 平台如何进入真实业务并留下可持续能力。
23. 作为访问者，我希望看到 TrueNorth 是决策智能产品族，而不是三个互相重复的产品宣传块，从而理解 Boss AI、Investor AI 和 TopSales AI 的共同基础。
24. 作为对投资、商业或 CEO 决策感兴趣的用户，我希望能够从 TrueNorth 总览进入对应二级页面，从而继续查看具体决策场景。
25. 作为访问者，我希望通过 Gallery 选择 Ontology、Solar、Refinery 或 Kazakhstan Energy 等 Demo，从而探索同一方法在不同场景中的延展。
26. 作为访问者，我希望 Gallery 按 Context、Digital Decision、Physical Operation 和 Sovereign System 分组，从而按关注主题选择 Demo，而不是按文件名浏览。
27. 作为访问者，我希望 Gallery Demo 在页内大尺寸 Modal 中打开，关闭后回到原来的叙事位置，从而保持页面上下文。
28. 作为访问者，我希望主 CTA 是探索运营闭环，而不是立即在多个产品之间选择，从而先理解方法再选择证明。
29. 作为潜在客户，我希望在页面结尾看到 `Start with one consequential loop`，从而知道下一步是选择一个重要工作流，而不是采购一整套产品。
30. 作为事实审阅者，我希望每条客户可见事实都能追溯到原始材料、实际运行结果或明确来源，从而避免把旧页面的推断或视觉细节当成承诺。
31. 作为项目维护者，我希望现有深页、Demo 和素材在新首页验证前继续保留，从而可以安全比较新旧叙事并逐步迁移。
32. 作为桌面端演示者，我希望首页在 1920px 视口下保持稳定的布局、清晰的层级和可预测的滚动节奏，从而用于正式演示。
33. 作为偏好减少动态效果的访问者，我希望系统在 `prefers-reduced-motion` 下仍能显示完整状态并即时切换节点，从而不依赖动画理解内容。

## Implementation Decisions

### 页面叙事顺序

首页按以下顺序组织，不按现有文件目录顺序复制：

1. **Immersive Founder Opening**：强沉浸品牌/创始人片头；运动表达机构、现实和系统关系。
2. **Core Claim**：在首段给出一句核心主张，明确 01.AI 解决重要决策与物理运营问题。
3. **Company and Team**：沿用当前公司段落作为内容基线。Kai-Fu Lee 使用已确认的露脸视觉；Ning Ning 保留文字身份和能力介绍；四项团队能力保留，但不铺成完整 About 页面。
4. **Operating Loop**：用 `Signal → Context → Decision → Action → Outcome` 作为全站统一语言。采用左侧固定系统视觉、右侧滚动章节；滚动推进当前状态，节点点击可跳转。
5. **Digital Proof**：Leadership AI 是首页数字主证明。保留大尺寸、可操作的内嵌工作台，提供推荐路径但不限制自由操作。加载状态、就绪状态和原地重试必须可见。
6. **Physical Proof**：Mining 是首页物理主证明。使用左侧固定 mine–rail–port 系统视觉、右侧章节滚动，表达主数据、动态排程、运营智能和验证结果；六步决策循环支持点击；提供现有 Mining 深页入口。
7. **Production Delivery Chain**：将 WorldWise 与 FDE 合并为从 Agent 构建、企业控制、部署到现场采用和可验收结果的一条能力链。
8. **Demo Gallery**：以较小探索卡展示 Ontology、Solar、Refinery 和 Kazakhstan Energy，并按 Context、Digital Decision、Physical Operation、Sovereign System 分类。Leadership AI 与 Mining 可作为主证明卡的再次入口，但不重复完整叙事。
9. **Engagement CTA**：以 `Start with one consequential loop` 收束，行动路径为 Align → Diagnose → Model → Build → Prove → Scale；主 CTA 为 `Explore the operating loop`。

### 内容优先级

- 一级：核心主张、五步运营闭环、Leadership AI、Mining。
- 二级：创始人/团队可信度、Ontology、WorldWise + FDE、TrueNorth 总览。
- Gallery：Ontology、Solar、Refinery、Kazakhstan Energy 的可操作入口。
- 深页：Boss AI、Investor AI、TopSales AI 以及现有产品/案例深页。

### 视觉与交互边界

- 目标画布为 1920px 桌面端，不在本规格内设计移动端布局。
- 强沉浸视觉必须表达状态、关系或结果变化；纯氛围运动不能承担核心信息。
- Sticky scrollytelling 只用于 Operating Loop 和 Mining，不让整页所有模块都采用同一种滚动结构。
- Leadership AI 工作台保持独立的大尺寸交互区域，不放入左右双栏的 sticky 容器。
- Gallery Demo 通过页内全屏或大尺寸 Modal 打开，关闭后返回原叙事位置。
- 动画必须提供 `prefers-reduced-motion` 静态状态和即时节点切换。

### 事实与文案边界

- 现有 HTML、文案索引和页面分析只作为候选素材库。
- 客户可见事实必须回到原始 PPT/来源材料、实际运行结果或明确可追溯证据核验。
- 不新增未经确认的客户名称、效果数字、路线图承诺、自动化能力或 fiduciary/legal authority 暗示。
- 不以模型数量、Agent 数量、页面数量或视觉复杂度作为价值主张。
- 客户可见页面使用英文；中文只用于内部规格、上下文和审阅记录。

### 首页叙事状态接缝

实现和验证围绕一个最高层的首页叙事状态接缝展开。它对外表现为：

- 当前滚动章节和对应视觉状态；
- 节点跳转后的章节状态；
- Demo Modal 的打开、关闭和返回位置；
- Leadership AI 的加载、就绪、失败和重试状态。

实现可以复用现有首页脚本、Mining 决策循环和 Leadership AI iframe，但规格不依赖具体类名、函数名或 DOM 结构。

## Testing Decisions

测试只验证访问者可观察到的外部行为，不锁定 CSS 类名、动画实现方式或具体 DOM 结构。

需要验证：

- 在 1920px 桌面视口下，页面按既定顺序呈现 Founder Opening、Core Claim、Company、Operating Loop、两大主证明、Delivery Chain、Gallery 和 CTA。
- 滚动 Operating Loop 时，右侧章节推进，左侧视觉状态同步变化；点击节点可跳到对应步骤。
- 滚动 Mining 主证明时，mine–rail–port 视觉与主数据、排程、运营智能和结果章节同步；六步循环按钮可切换内容。
- Leadership AI iframe 能显示加载、Product ready 和失败重试状态；推荐路径入口可用，工作台内部导航不离开首页上下文。
- Gallery 各分类入口打开正确 Demo 的 Modal；关闭 Modal 后页面回到原叙事位置。
- `prefers-reduced-motion` 下不依赖持续动画即可访问所有章节、节点和状态信息。
- 所有主 CTA、节点、Modal 关闭、深页入口和二级产品入口可通过鼠标和键盘操作。
- 页面加载资源失败时不出现遮挡主内容的永久 loading 状态。
- 英文客户可见文本无意外中文、内部注释、临时占位词或未经核验的承诺。

现有验证基础包括：首页交互脚本、Mining 决策循环、Leadership AI 跨页面导航与产品就绪检查、Demo Modal 行为和客户可见语言审计。新实现应优先扩展这些外部行为验证，而不是新增平行测试体系。

## Out of Scope

- Boss AI、Investor AI、TopSales AI、WorldWise、FDE、Kazakhstan Energy 等二级页面的重新设计。
- 现有二级页面、Demo 或素材的删除、归档或大规模重写。
- 移动端布局、移动端交互、响应式验收和窄屏性能优化。
- 新增未经来源核验的客户案例、经营结果、市场数字或产品路线图。
- 重新定义 01.AI 的品牌定位、公司历史或产品命名。
- 用视频替代 Leadership AI 的真实可操作工作台。
- 将所有 Demo 连续嵌入首页或把所有产品做成等权卡片。
- 为了视觉效果引入没有业务含义的持续动画、自由探索 3D 或复杂 WebGL 场景。

## Further Notes

- 当前首页的公司段落、现有 Mining 深页、Leadership AI 工作台和 Demo 运行时是本规格的事实与表达基线；新首页先重组信息，不先重写所有旧资产。
- 由于仓库没有远程 GitHub 或已配置的 issue tracker，本规格先作为项目根目录的独立文档保存；进入多任务实现前再配置任务拆分流程。
- 下一步应先做首页叙事状态接缝的 1920px 可运行原型，重点验证两条 sticky 滚动线是否能同时保持沉浸感和理解度；原型通过后再进入正式 HTML 实现。
