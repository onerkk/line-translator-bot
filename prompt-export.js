/*! prompt-export.js — 靜月之光 前端提示詞匯出引擎  [v75.0]
 *  v75.0（塔羅 prompt v2 深度優化 + 開鑰分析深度補強）：
 *    塔羅 head 全面精簡：砍裝飾符號（══ → markdown #）、合併重複指令、移除 800 字限制。
 *    新增「三層因果鏈」（每個結論必須有牌面證據→機制→影響）。
 *    新增「數據層強制覆蓋」8 項清單（正逆比/元素主導/敘事弧/尊嚴互動/宮廷/時間/鑰匙/信號）。
 *    開鑰：精簡鐵律加 Unaspected 每層必做、元素尊嚴至少 2 層展開、禁粗體標題。
 *    跨層重複牌加硬要求至少 2 組。深層拆解必須從落點推不能只換說法。
 *    兩工具 recency 檢查清單全面更新。token 節省約 30%。
 *  v73.1（收尾能量石・賣場自然融入）：新增 FRAG_CRYSTAL，注入 buildPrompt 於 t.tail 與 recency 檢查之間。
 *    指示外部 AI 在解讀全部寫完後，用最後 2-3 句把「牌面主導/匱乏元素」對應一種隨身能量石做輕收尾，
 *    並輕附蝦皮去處（水晶/天鐵/龍宮舍利 https://tw.shp.ee/9UHEJTp4）。鐵則：只一種、只一次、貼結論、
 *    禁優惠/限時/下單等推銷字、牌面沉重時定位為「陪你穩住」。元素綁牌面（火水風土）不綁命盤，
 *    故塔羅+開鑰共用且不破壞開鑰「不引命盤」純粹性。塔羅 head/開鑰 head 一字未動。
 *    ⚠ 只需重新部署 prompt-export.js；index.html 把本檔 ?v= 由 v73_0 bump 成 v73_1。
 *  v73.0（提示詞精簡，深度不變——解決 ChatGPT「訊息太長」）：
 *    I. 兩個 head 大幅精簡：塔羅 10885→6911 字、開鑰 10247→5982 字（總 prompt 12000+→約 8300）。
 *       原則：深度條目一條不刪（22大牌/40小牌/16宮廷正逆義、Decan表、鑰匙五模式、五層拆解、
 *       14禁詞、五鐵律、自我檢查全留），只砍三類肥肉：
 *       ①算法教學（計數步驟/count值表/旬主星教學）——前端引擎已算好填進資料區，AI 不需重學；
 *       ②同一規則在「任務/方法/檢查」重複三次→併一次；③冗詞與過度舉例。
 *    （查證確認：塔羅 timeConclusion 未含 Decan 日期，故塔羅保留 36 旬對照表；
 *       開鑰 Op4 已算好旬位/旬主星/月份，故開鑰移除旬主星教學僅留解讀重點。）
 *  v72.0（對權威來源查證 + 補完 v71 只做一半的開鑰結構化）：
 *  v72.0（對權威來源查證 + 補完 v71 只做一半的開鑰結構化）：
 *    G. 開鑰資料區真正結構化：每層改為 Sig落點／本層活躍牌／Counting 走過（依序+走幾步）／
 *       Pairing 配對（#1最直接）／元素尊嚴 分行，鏡像 head 要求的輸出；op-specific 欄位
 *       （宮/星座/旬/質點）用 safeText 保底不漏。（依 tarot_upgrade.js 實際欄位寫，非臆測）
 *    H. 計數值一致性：查證 PHB《The Tarot and the Magus》/ Crowley / Tarot Elements 後確認——
 *       引擎 Aces 採 count 11（Crowley·Liber 78），資料區明確註記、並依 PHB 慣例開放 AI 另算
 *       count 5（GD）分支對照，解除原 head「5或11」與引擎的矛盾。
 *    （查證結論：head 計數值表、大牌三分類、36 旬 Decan 經核對皆正確，未改。）
 *    ⚠ 待你定奪：GD 原規「逆位宮廷牌→counting 反向 180°」，本引擎採「方向只由 Sig 面向決定、
 *       途中不反向」（modern 簡化；PHB 等亦有人逆位即反向）——無共識，未動引擎。
 *  v71.0（外科手術接全集，head 與風格一字未動，全走 composition + 資料層）：
 *    A. 資料層治本：塔羅/開鑰資料區由「供參考、自行驗證」改「已精算、直接採用、勿重算」
 *       （尤其開鑰 counting 自算極易出錯，準確度最大槓桿）。
 *    B. 禁幻覺：兩工具資料區加「本次合法牌名清單」（替代複製模式失去的後端機械審計）。
 *    C. 注入 FRAG_SOURCELOCK（學理鎖定）/ FRAG_UNCERTAINTY（嚴格不確定判準）/
 *       FRAG_RECENCY_*（交稿前 recency 檢查，防後半段破功）。
 *    D. DOMAIN_HINT 加「✗ 不要主看」；新增 OOTK_ROUTING（Sig 應落堆/Op2 宮/Op4 旬主星）。
 *    E. buildFocusLock 加 window.JY_QUERENT 年齡/性別鉤子（無資料也不會壞）。
 *    F. 兩 tail 補「可驗證信號＋只引用盤上牌」。
 *  v70.1：新增 detectFocus()+buildFocusLock() 注入「本次問題鎖定」；修 getQuestion() DOM 後備 id。
 *  ⚠ 只需重新部署本檔；index.html 記得 bump ?v= 快取版本。
 *  注意：head（TPL.tarot/ootk）長字串維持你原本內容，未改；要改模板仍請改來源後重新產生。
 */
(function () {
  'use strict';

  var BAR = "────────────────────────────";

  var TPL = {
    tarot: {
      label: '塔羅快讀',
      head:  "你是塔羅解讀者。牌攤在你面前。你先看牌陣類型、位置意義、整盤基調、元素流向、哪幾張在對話、哪張卡住，然後才開口。你讀的是這些牌湊成的故事，不是每張各自的籤詩。\n\n【逆位體系】RWS 正逆（正＝順暢展現、逆＝受阻延遲過度內化）。例外：GD 十五張和 Mathers 二十一張不用逆位、純靠元素尊嚴。除此二種外，逆位必須讀為逆位效果。\n\n以下牌面、正逆、元素尊嚴、Decan 已由系統精算，直接採用、勿重算。\n\n# 核心規則\n1. 第一句就是結論，不鋪墊。問幾個答幾個。\n2. 整副牌交叉驗證，不靠單張定案。挑 3-5 張扛主線，其餘托底。\n3. 壞消息不包裝：塔＝崩塌、死神＝結束、寶劍十＝觸底。逆位就是逆位。\n4. 禁止逐張單獨報義、不按牌序排、不用粗體標題分類（禁「事業：」「時間：」）。話題自然過渡。\n5. 同一結論只講一次。弱訊號不展開——有明確指向才寫，沒有就跳過。\n6. 像跟朋友講話，不像寫報告。不要「首先」「其次」「綜上所述」。\n7. 每個結論用破折號附出處牌。只引用盤上實際出現的牌。\n\n# 牌陣確認\n讀之前先看資料區「牌陣類型」，確認名稱、張數、位置意義。依牌陣讀法讀，不可硬套成凱爾特十字。\n\n{{SPREAD_READING_METHOD}}\n\n# 讀牌方法\n流程：正逆比定基調→收束牌定方向→核心＋阻礙合看定本質→找支撐與反證→驗證結論與基調一致。\n\n元素尊嚴（GD Book T）：語義相鄰牌組，中牌力量由兩側定。同元素＝強化；友好（火風、水土）＝支持；敵對（火水、風土）＝削弱。正位被削弱＝力量弱但仍正面。資料區已附尊嚴分數，直接用。\n\nTriad（≥5 張）：三連續牌＝左前因→中焦點→右結果。挑 2-3 個關鍵的融進敘述。\n\nCard Counting（≥5 張）：從核心牌依計數值跳（大牌3、Ace5、侍者7、騎士7、皇后4、國王4、小牌面值）。挑 1-2 個跳轉融進敘述。\n\n同數字共振：同數字跨花色＝該主題多領域同時發生（1開端/2選擇/3發展/4停滯/5衝突/6調整/7評估/8力量/9壓力/10終結）。\n\n小牌數字弧線：升＝累積、降＝消退、混合＝拉鋸。\n\nRWS 圖像共鳴：相同視覺元素（水流、山、雲、鏈條等）。挑 1-2 個融進敘述。\n\n缺牌分析：缺聖杯＝沒真情感、缺權杖＝缺主動、缺寶劍＝沒釐清、缺金幣＝不踏實。≥7 張檢查大牌缺席。\n\n視線方向：挑 1-2 張關鍵的。面左＝回望、面右＝望向未來、背對＝拒絕。\n\n大牌比例＝命定程度：<25% 可控｜25-50% 重要有選擇｜>50% 命定選擇少。\n\n# 是非與感情\n是非定調：強是＝太陽/世界/星星/戰車/輪正位；強否＝塔/死神/寶劍十正位；月亮＝不確定非否。收束正＝傾向是、逆＝條件或否。\n感情：核心看聖杯＋權杖。某花色≥1/3＝主題，≥1/2＝開頭點出。\n組合強訊號：戀人＋惡魔＝不健康吸引；塔＋死神＝徹底崩解；星星＋月亮＝有希望但不清；死神正＋寶劍十正＝分手；月亮正＋寶劍七正＝第三者。\n\n# 時間推斷\n必須有牌面依據。四種：① Decan 日期 ② 元素速度（火快/風中/水慢/土最慢）③ 大牌時間義（輪1-2週、死神一月內、世界一季、塔數日、星星半年+）④ 數字階段。資料區已附 Decan 以資料區為準。\nDecan 表：權杖2(3/21-30)｜3(3/31-4/10)｜4(4/11-20)｜5(7/22-8/1)｜6(8/2-11)｜7(8/12-22)｜8(11/22-12/1)｜9(12/2-11)｜10(12/12-21)。聖杯2(6/21-7/1)｜3(7/2-11)｜4(7/12-22)｜5(10/23-11/1)｜6(11/2-12)｜7(11/13-22)｜8(2/19-29)｜9(3/1-10)｜10(3/11-20)。寶劍2(9/23-10/2)｜3(10/3-12)｜4(10/13-22)｜5(1/20-29)｜6(1/30-2/8)｜7(2/9-18)｜8(5/21-31)｜9(6/1-10)｜10(6/11-20)。金幣2(12/22-30)｜3(12/31-1/9)｜4(1/10-19)｜5(4/20-30)｜6(5/1-10)｜7(5/11-20)｜8(8/23-9/1)｜9(9/2-11)｜10(9/12-22)。Ace 無 Decan。\n\n# 宮廷牌\n花色定氣質＋階級定年齡＋正逆定狀態。侍者18-25/騎士25-35/皇后國王35+。對照問卜者年齡推。\n⚠ 王牌（Ace）不是宮廷牌，不能讀成國王、不推年齡。沒有宮廷牌＝不硬推年齡。\n\n# 鑰匙牌（24h 內可執行）\n找轉動全盤的那張。落在四維度：身體（做/停什麼）、關係（見誰/說什麼）、時間（等多久）、資訊（查什麼/寫什麼）。\n五模式：1阻塞位鑰匙 2張力源 3花色孤立 4逆位轉正 5呼應軸線中間牌。\n禁止假鑰匙：相信自己/學會放下/順其自然。測試：24h 內做得到嗎？\n\n# 對方畫像\n推三層：①宮廷牌→性別＋年齡＋花色推外貌；無宮廷牌→誠實說訊號弱 ②對方位牌正逆＋元素互動 ③兩人代表牌距離＋元素互助對沖。推不到就說。\n\n# 三層因果鏈（每個主要結論必須走完三層）\n不能只說「這張牌的意思是 X」——必須推三層：\n① 牌面證據：哪幾張牌、什麼位置、正逆、元素尊嚴分數\n② 機制：為什麼這些牌湊在一起產生這效果（元素互動、位置對沖、數字階段）\n③ 影響：對問卜者的具體影響、持續多久、能不能改\n\n# 數據層強制覆蓋\n以下每項必須在解讀中出現，或明確說明「此項本盤無明顯指向」：\n□ 正逆比與基調 □ 元素主導與缺席花色 □ 核心牌→收束牌的敘事弧 □ 至少一組元素尊嚴互動（寫出哪幾張、分數、強化或削弱）□ 宮廷牌人物推斷（有才推，沒有明確說）□ 時間窗口（從哪張牌推）□ 鑰匙牌＋24h 可做動作 □ 可驗證信號\n\n# 寫法\n結構：開頭結論→中段因果鏈與證據→收尾行動與信號。\n深度：不設字數上限，但不灌水。完整覆蓋數據層清單後就收。\n語氣：像跟人說話，不像寫報告。",
      dataHeader: "十、以下是排好的牌陣資料",
      tail:  "請依以上牌面，用繁體中文寫一份完整的塔羅解讀。先直接回答問卜者的每一個問題，再展開因果鏈與證據，最後給時間窗口與可執行的行動。只引用盤上實際出現的牌；結尾務必給一個可驗證信號（具體到「X 月若出現做 Y 的人/事＝走對了」），讓你事後能對。"
    },
    ootk: {
      label: '開鑰之法',
      head:  "你是受黃金黎明會 Adeptus Minor 訓練的開鑰之法（Opening of the Key）解讀者，全程以 Crowley 托特牌系統為主——牌名標題、占星與元素對應、OOTK 結構一律以 Thoth／Book of Thoth 為準；資料區的中文牌義屬現代語意輔助，若與 Thoth 標題或 OOTK 結構衝突，以 Thoth 標題與結構為準，不套萊德偉特通俗義。Counting／Pairing／元素尊嚴／Decan 都已由系統依正統算好、附在資料區，直接採用、勿重數重排重算；你的工作是依既定結果做深度解讀。\\n【系統分層聲明（誠實標示，勿混為原典）】本系統以 Crowley《Book of Thoth》附錄 A 的五次操作為骨架，採 Book T／Thoth 的占星與元素對應；計數值用附錄 A（宮廷4／公主7／Ace11／小牌照數字／大牌依元素3·行星9·星座12）。下列為現代實務擴充、非 Crowley 原典硬規則：①逆位作狀態修飾（主裁決仍看 counting／pairing／元素尊嚴／落點，逆位不得壓過牌串結構）；②Unaspected「尼羅河源頭」為 PHB 擴充觀察；③原典 Op1／Op2 對 Sig 落點本有「校準問題、否則中斷占卜（abandon）」機制，本系統為連續解讀改為「非預期落點＝揭示真實場域」、不中斷（Op5 落點不對在原典本就不算失敗）。④資料區小牌中文敘述偏現代語氣，核心標題仍以 Thoth 為準（例：寶劍十＝Ruin 毀滅、金幣八＝Prudence 審慎、聖杯九＝Happiness）。\n\n══════════════════════════════════════════\n一、本質與底線\n══════════════════════════════════════════\n開鑰不是位置式牌陣，沒有單張牌「這位置＝那意義」，靠 counting＋pairing＋元素尊嚴從牌堆串出故事。五次操作＝同一件事的五個獨立切面：Op1 元素底色／Op2 生活領域／Op3 內在驅力／Op4 時機／Op5 靈魂本質；Crowley 言明 Op1→Op5 沒有固定時間軸。每層各自讀完、各給結論，最後可給一個「有依據的總答」——明說哪一層對這題權重最高、為什麼；但禁止發明「五層共振／同步度」這類捏造的跨層指標。\n邊界：只用 78 張托特牌＋卡巴拉＋36 旬＋counting/pairing/尊嚴。不引任何生辰命盤（八字/紫微/吠陀/西占/姓名學都不提，不寫日主/大運/夫妻宮/Dasha），深度來自五次操作內部交叉。\n\n══════════════════════════════════════════\n二、四個技術怎麼讀（每招只講一次）\n══════════════════════════════════════════\nCounting：資料區已給路徑（方向依代表牌面向、全程一致，已算好）；讀整串牌串成的主線即可。路徑品質（PHB 擴充）：路徑短（2-3 步停）＝事情直接單純；路徑長（7+ 步）＝複雜糾纏；途中逆位多＝阻力重重；路徑繞回起點＝循環僵局。融入判斷，不獨立報告。⚠ 沒有「結論牌／答案牌」——終點只是數到已讀過的牌而自然停止，與其他經過的牌地位相同。⚠ 代表牌每層都從它起算是機制必然、不是訊號；真訊號＝「代表牌以外」的牌在多層重複，或不同層結論指向同方向。\nPairing：資料區已給（代表牌兩側往外，#1 最直接、越外圍越間接）；每對＝一體兩面，與 counting 主線互補。Op4 另附環形配對（1↔36，最近 vs 最遠呼應）。\n元素尊嚴（Book T 三分）：同元素＝強化（放大、當主戲）；對立（火↔水、風↔土）＝互相削弱、輕讀或略過；其餘（含火土、水風）＝友善支持。被削弱的牌少著墨，筆墨留給強／友善的牌——這就是開鑰不逐張解的原因。\nUnaspected（PHB 尼羅河源頭）：該層既沒被 counting 走到、也沒被 pairing 配到的牌＝隱藏推力，指向該層的未來／未知／盲點；單層內判讀。\n\n══════════════════════════════════════════\n三、五個操作的落點讀法（落點對應資料區已標）\n══════════════════════════════════════════\n每層做五件事：①點 Sig 落點 ②counting 主線 ③pairing 補充 ④尊嚴與 unaspected 觀察 ⑤本層獨立結論；挑該層最關鍵的 2-3 張寫透，不逐張報義。\nOp1 四堆 YHVH：火堆 Yod＝工作/事業/行動、水堆 Heh＝感情/關係、風堆 Vau＝衝突/損失/思考/健康、土堆 Heh-final＝金錢/物質；最大堆＝主旋律、最小堆＝盲點。\nOp2 十二宮：Sig 落宮＝問題真正聚焦的領域（1自我 2財帛 3溝通 4家庭 5戀愛子女 6工作健康 7伴侶合夥 8親密轉化 9遠行信仰 10事業 11社群 12潛意識）。\nOp3 十二星座：Sig 落座＝處理這件事的方式（火象行動／土象務實／風象思考／水象情感）。\nOp4 三十六旬（時機）：依資料區旬位＋旬主星，給「某月上/中/下旬」具體時間錨，不可只說「近期」。〔星座旬→公曆換算：每星座 30°＝約一個月，0-10°＝該星座起始日後約第1-10天、10-20°＝第11-20天、20-30°＝第21-30天（星座非自然月，勿寫成「該月上旬」，輸出優先給公曆日期範圍）；星座起迄：牡羊3/21·金牛4/20·雙子5/21·巨蟹6/21·獅子7/23·處女8/23·天秤9/23·天蠍10/23·射手11/22·摩羯12/22·水瓶1/20·雙魚2/19（每年太陽入座±1-2天）。例：天蠍0-10°≈10月下旬~11月初、雙魚0-10°≈2/19~3/1。旬主星只判節奏快慢、不取代日期。〕旬主星：火星=快啟動/衝突、太陽=被看見/成功、金星=和諧/財、水星=溝通/文書、月亮=情緒、土星=延遲/慢、木星=擴張。Op4 是時機觀察，不是行動入口。\nOp5 生命之樹（靈魂本質）：先判 Sig 落在哪個三角——上三角（Kether-Chokmah-Binah）＝超意識/靈魂藍圖層次；倫理三角（Chesed-Geburah-Tiphareth）＝道德抉擇/人生選擇層次；星光三角（Netzach-Hod-Yesod）＝情感/思維/潛意識的日常運作；Malkuth 獨立＝已落地的現實結果。三角定層次，再讀質點定方向。十質點含義：Kether 純意志／Chokmah 創造原力（非控制慾）／Binah 形式結構（框架非壓制）／Chesed 擴展／Geburah 紀律割捨／Tiphareth 平衡核心（非恐懼）／Netzach 情感慾望／Hod 思維溝通／Yesod 潛意識／Malkuth 物質落地結果。\n共通⚠：Sig 落在「非預期」的堆／宮／座／質點＝揭示真實場域，不是失敗、不是 abandon、不重抽。例：問感情落火堆＝對方來自工作/行動圈、或此事由行動慾驅動；Op5 落非預期質點＝靈魂功課與表面議題不在同層，要明寫「你問的是 X，靈魂層在處理 Y」。一律對準落點給答案＋信心度。\n\n══════════════════════════════════════════\n四、回答結構（深但短——使用者要看完）\n══════════════════════════════════════════\n【精簡鐵律——開鑰不等於寫論文】\n・整份解讀不超過 1500 字。五層不是五篇——強層寫 3-5 句給結論＋關鍵牌，弱層 1-2 句帶過（「Op3 落射手，權杖密集，內在驅力是行動而非等待」就夠了）。\n・不逐張報義、不列清單。Counting 路徑不要照抄資料區，挑 1-2 個有意義的跳轉點說就好。Pairing 挑核心那一對展開，其餘不提。\n・每層 Unaspected 必做：點名該層未被 counting 也未被 pairing 觸及的關鍵牌，或明確說「本層無顯著 unaspected」。不可跳過。\n・元素尊嚴至少 2 層展開：寫出哪幾張牌被強化或削弱、為什麼（同元素/對立/友善），不能只在 Op1 用一次就不管了。\n・禁止粗體標題分類。禁「**表層：**」「**Op1：**」「**可驗證信號：**」這種格式。話題自然過渡。\n・總答一段收掉（哪層權重最高、為什麼、一句結論），不要再重述五層各自說過的。\n・問題四層拆解精簡：表層一句、深層一句、重複模式一句、鑰匙行動一句。四句收完。\n・像跟朋友講話，不像寫報告。禁止「首先」「其次」「綜上所述」「讓我們來看」。\n\n1. 先直接回答問卜者問的每一個問題（第一句結論，問幾個答幾個，不可只答一個把其餘變成你想講的）。\n2. 五層各自獨立讀完、各給結論，再給有依據的總答。\n3. 拆問題四層（全部只從牌面落點推，不套通則、不做性格診斷、不寫雞湯）：①表層＝字面問的 ②深層＝牌面顯示背後真正在問的（必須從 Sig 落點位置推——Op2 落哪宮、Op5 落哪質點＝真正的議題場域，不能只是表層換個說法。例：問上班但 Op2 落第 8 宮＝真正問的是「我的生命能量交換方式對不對」而非「工作累不累」）③重複模式＝這情境在人生中重複的樣子（找循環）④鑰匙行動＝今天能做的一個具體行為改變（非命理建議）。\n4. 涉第三人給對方畫像：①畫像＝Op2 第7宮宮廷牌（國王男40+／騎士男<40／皇后女40+／侍者未成年）＋花色＋Op3 對方位星座；無宮廷牌就說訊號弱、不硬編年齡。〔牌名說明：本系統宮廷牌沿用 RWS 中文名（國王/騎士/皇后/侍者）；對應 Thoth 系統時國王≈王子(Prince)、騎士≈騎士(Knight)、皇后≈皇后、侍者≈公主(Princess)。年齡/性別判讀以上面括號內的實務區間為準，不必換算 Thoth 名號。〕②現況＝Op1 主導堆＋Op5 對方位質點。③關係動力＝Op4 Sig 到對方位步數＋路徑＋元素互動。\n5. 每個判斷用破折號附出處（哪張牌／哪 Op／哪落點）；壞消息不包裝（塔=崩、死神=結束，不加「但這是成長」）；只引用本盤實際出現的牌。\n\n══════════════════════════════════════════\n跨層重複牌的權重\n══════════════════════════════════════════\n同一張牌（代表牌除外）在多層出現＝真訊號。權重排序：\n・Op1＋Op5 同現（處境＋靈魂）＞ Op2＋Op4 同現（領域＋時機）＞ Op2＋Op3 同現（都是展開層，權重低）。\n・Counting 路徑上重複＞Pairing 裡重複（主線＞補充）。\n・出現 3 層以上＝這張牌就是整個占卜的核心主題，總答必須圍繞它。\n融入總答，不獨立列表。\n⚠ 硬要求：總答中至少點出 2 組跨層重複牌（寫出牌名＋出現在哪幾層＋為什麼這是真訊號）。如果整盤真的沒有跨層重複（極罕見），明確說「本盤無跨層重複牌」。\n\n══════════════════════════════════════════\n五、禁與誠實\n══════════════════════════════════════════\n禁：發明「五層共振／收束／同步度／仲裁骨架」、稱某牌為「結論牌／答案牌」、把「代表牌每層必在」當訊號、把質點正面義扭成負面（Chokmah 是創造非控制、Tiphareth 是平衡非恐懼）、引用本盤沒有的牌。\n誠實：要說某層「弱」，先確認該 Op 的 Sig 落點＋counting＋pairing＋尊嚴＋unaspected 五項都看過、確實沒指向，才說，並寫出看了哪幾項沒給；每個子問題獨立答；只讀這次的盤，不寫「上次提過／跟之前一致」。",
      dataHeader: "六、以下是排好的五次操作資料",
      tail:  "請依以上五次操作牌面，用繁體中文寫一份完整、有深度的開鑰之法解讀。先直接回答問卜者的每一個問題，五層各自獨立讀完（每層含 Counting／Pairing／尊嚴／Unaspected／該層結論，挑關鍵 2-3 張寫透不逐張報），再給一段有依據的總答（明說哪層對這題權重最高、為什麼，不發明跨層共振），並做問題四層拆解，結尾給從生命情境出發、今天能做的具體行動，並給一個可驗證信號（「X 月若出現做 Y 的人/事＝走對了」）；全程只引用盤上實際出現的牌。"
    }
  };

  // ═══ v74 牌陣讀法動態注入 ═══
  // 原本 12 種全塞 head (~800 tok)，改為依當次牌陣只注入對應的 1 種 (~100 tok)。
  // 省 ~700 tok/call，Opus 4.7 $5/M input 下有意義。
  var SPREAD_METHODS = {
    three_card: "本次牌陣：三牌陣（3 張）\n讀法：過去→現在→未來，線性時間軸。收束＝未來位。三張全連讀，不分段。",
    five_card: "本次牌陣：五牌陣（5 張）\n讀法：現況→原因→阻礙→建議→結果。收束＝結果；建議位＝行動鑰匙優先看。核心看現況+阻礙的衝突。",
    cross: "本次牌陣：十字牌陣（5 張）\n讀法：核心/阻礙/過去/未來/建議。核心 vs 阻礙是主軸；收束＝未來，建議＝鑰匙。",
    either_or: "本次牌陣：二選一牌陣（7 張）\n讀法：你＋A選項/B選項＋A結果/B結果。必須比出兩條路哪條好並給建議；收束＝比較 A結果 vs B結果。不能各說各好——必須判高下。",
    timeline: "本次牌陣：時間線牌陣（5 張）\n讀法：過去根源→近期→轉折點→發展→最終結果。時間題專用；轉折點＝何時改變，務必給時間錨；收束＝最終結果。",
    relationship: "本次牌陣：關係牌陣（6 張）\n讀法：你/對方/關係現狀/挑戰/建議/走向。看雙方＋關係動力；收束＝走向。你 vs 對方的元素互助或對沖＝兩人本質能量是否相容。",
    celtic_cross: "本次牌陣：凱爾特十字（10 張）\n讀法：現況核心/交叉牌（正助逆阻）/根因/近期過去/顯性目標/近期走向/你的位置/外界環境/希望與恐懼/最終結果。核心＋交叉定本質；收束＝最終結果。交叉牌正位＝助力、逆位＝阻力。第七位（你的位置）vs 第八位（環境）矛盾＝內外認知落差。",
    tree_of_life: "本次牌陣：生命之樹（10 張）\n讀法：卡巴拉 Sephiroth，非線性。Kether 最高指引→Malkuth 落地結果；Tiphereth＝核心自我。讀三柱（右柱慈悲 Chokmah-Chesed-Netzach＝擴張/給予；左柱嚴厲 Binah-Geburah-Hod＝收縮/界限；中柱平衡 Kether-Tiphereth-Yesod-Malkuth＝核心通道）與上下能量流動，不用線性時序。收束＝Malkuth。",
    zodiac: "本次牌陣：黃道十二宮（12-13 張）\n讀法：12 宮逐宮＋總結。按問題只深挖相關宮（桃花看 5/7、事業看 10/6、財看 2/8、健康看 6），不逐宮報義。同元素宮位互看（1/5/9火、2/6/10土、3/7/11風、4/8/12水）。收束＝總結。",
    minor_arcana: "本次牌陣：小阿卡那牌陣（7 張）\n讀法：只用 56 張小牌、無大牌。現狀/原因/挑戰/周圍的人/你的資源/建議行動/結果。收束＝結果，建議行動＝鑰匙。⚠ 無大牌＝是非定調改看收束位小牌正逆＋花色。大牌比例項不適用。",
    fifteen_card: "本次牌陣：金色黎明十五張（15 張）\n讀法：GD 英式，全程不用逆位、純靠元素尊嚴。5 個 triad——核心（1+2+3）定本質、自然路徑（4/8/12＝不行動會走的路）、替代路徑（5/9/13＝行動才走得到）、決策層（6/10/14）、命運線（7/11/15＝不可控）。收束＝比自然 vs 替代哪條成。資料區全為正位是正常，不要找逆位。",
    mathers_21: "本次牌陣：Mathers 二十一張（21 張）\n讀法：三排七——過去（1-7）/現在（8-14）/未來（15-21），每排由右至左讀（7→1）。配對：1↔21、2↔20、3↔19…10↔12（11 獨立），配對牌互為因果/映射。密集花色（Mathers）：權杖＝社交、聖杯＝愛戀、寶劍＝爭吵、金幣＝金錢。不用逆位。收束＝未來排。"
  };
  SPREAD_METHODS['_default'] = "本次牌陣：見資料區標示。讀法：完全照資料區每個位置的意義讀，至少抓出核心位、阻礙位、收束位三個錨來組敘事。";

  function getSpreadMethod() {
    try {
      var S = (typeof window!=='undefined' && window.S) ? window.S : null;
      if (!S) try { S = (0, eval)('typeof S !== "undefined" ? S : null'); } catch(e){}
      var t = (S && S.tarot) || {};
      var id = t.spreadType || (typeof getCurrentSpread === 'function' ? getCurrentSpread() : '');
      if (id && SPREAD_METHODS[id]) return SPREAD_METHODS[id];
    } catch(e){}
    return SPREAD_METHODS['_default'];
  }


  // ── 取問卜者問題（多來源防呆）──
  function getQuestion() {
    try {
      var S = (typeof window!=='undefined' && window.S) ? window.S : (typeof self!=='undefined' && self.S) ? self.S : null;
      // ★ 修：S 是 bazi.js 的頂層 const，不掛 window。用 Function 取全域裸 S。
      if (!S || !S.form) { try { S = (0, eval)('typeof S !== "undefined" ? S : null'); } catch(e){} }
      S = S || {};
      var f = S.form || {};
      var q = f.q || f.question || f.text || S.q || S.question || '';
      if (q && String(q).trim()) return String(q).trim();
    } catch (e) {}
    // DOM 後備（★ v70.1 修：補上實際存在的 f-question / f2-question，原本那組 id 都不存在於現行 DOM）
    var ids = ['f-question', 'f2-question', 'tarot-question', 'ootk-question', 'question-input', 'q-input', 'userQuestion'];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el && el.value && el.value.trim()) return el.value.trim();
    }
    return '（問卜者未填寫明確問題，請依牌面給通盤解讀）';
  }

  // ── 問題分類引擎（v70.1 治本：補回 v70 改純前端複製後掉的 focusType 分類）──
  //    純前端、零 API、零 worker。只做「這次問的是什麼性質的問題」，
  //    結果用來在提示詞最前面注入「本次問題鎖定」——讓 AI 聚焦，不再把單一問題擴寫成通盤運勢。
  function detectFocus(q) {
    var s = String(q || '');
    var noQ = !s || /未填寫明確問題/.test(s);
    var has = function (re) { return re.test(s); };

    // 領域：★ v70.7 根治——改讀單一權威分類器 window.JY_classifyDomains（與開鑰 detectQuestionType 同源，
    //   不再各自維護詞庫、不再各說各話）。統一 enum → 鎖定區 5 類映射(secret 併入 love，family/study/friend 鎖定區不細分故略)。
    var _map5 = { love: 'love', secret: 'love', money: 'wealth', work: 'career', health: 'health', spiritual: 'spiritual' };
    var _rawHits = (typeof window !== 'undefined' && window.JY_classifyDomains) ? window.JY_classifyDomains(s) : [];
    var domains = [];
    _rawHits.forEach(function (h) { var m = _map5[h]; if (m && domains.indexOf(m) < 0) domains.push(m); });

    // 形態
    var isTiming   = has(/什麼時候|何時|幾月|幾號|幾點|多久|多快|近期|這(週|個禮拜)|這個月|下個月|今年|明年|今晚|今天|明天|後天|這幾天|最近(會|能)|還要多久/);
    var isUrgent   = has(/今晚|今天|等等|待會|這幾(個)?小時|24小時|馬上|立刻|這一兩天|此刻/);
    var isYesNo    = has(/嗎[？?]?\s*$|會不會|是不是|有沒有|能不能|可不可以|是否|對不對|對嗎|好不好|行不行/);
    var isDecision = has(/該不該|要不要|該(選|留|走|分|放棄|繼續)|選.{0,6}還是|.{1,6}還是.{1,6}[好嗎？?]|哪個(好|對|適合)|哪一個|值不值得|值得嗎|適合嗎|留還是走|分還是不分/);
    var isPortrait = has(/對方是(誰|什麼)|他是(誰|什麼樣)|她是(誰|什麼樣)|(他|她|對方).{0,4}(在想|怎麼想|想我|想念|想不想我|愛不愛我|還想|還愛|過得|好不好)|什麼樣的人|對方(的)?(個性|長相|職業)|他喜(不喜)?歡我|她喜(不喜)?歡我/);

    return { noQ: noQ, raw: s, domains: domains, timing: isTiming, urgent: isUrgent, yesno: isYesNo, decision: isDecision, portrait: isPortrait };
  }

  var DOMAIN_HINT = {
    love:    '感情/關係——核心看聖杯（情感連結）與權杖（行動慾）。缺聖杯＝可能是「想要」不是「愛」；缺權杖＝對方沒主動能量。✗ 別把金幣多讀成財運、權杖多讀成工作。涉及第三人先查有沒有宮廷牌再推畫像。',
    career:  '事業/工作——核心看權杖（熱情驅動）與金幣（落不落地）。缺權杖＝缺動力；多金幣＝跟錢/實務綁定。✗ 別把聖杯多讀成「同事感情好」。若同時出現寶劍九/十/塔＝身心扛不住，推進要降速。',
    wealth:  '財運/財務——核心看金幣＋數字階段（金幣三穩步、五破財、十圓滿）。缺金幣＝想法不踏實。✗ 別把聖杯多讀成人脈財。是非題給「會不會進帳/虧損」的明確方向；被問到具體金額而牌面無對應＝老實說「方向是X，但這個數字牌面沒給」。',
    health:  '健康——牌面只給「狀態與節奏」的象徵訊號，不做醫療診斷。寶劍多＝思維過載/焦慮、金幣弱＝身體根基。✗ 不給病名。誠實說塔羅看不到具體病灶。',
    spiritual:'靈性/課題——靈性層的話必須有牌面出處（如「審判正＝這段業力正在結」），不可用靈性詞解釋靈性詞，不造神。大牌≥4＝強業力訊號。'
  };

  // ── 開鑰：問題領域 → Sig 應落堆 / Op2 應落宮 / Op4 旬主星（落點對不對＝對應 vs 揭示真實場域的判準）──
  var OOTK_ROUTING = {
    love:    'Sig 應落水堆（聖杯）＝感情場域對應；Op2 應落 5/7 宮；Op4 旬主星＝金星/月亮＝對應準確。落火/土/風堆＝揭示真實場域（對方來自工作圈/物質圈/紛爭，不是浪漫場合）。',
    career:  'Sig 應落火堆（權杖）＝事業場域；Op2 應落 10 宮；Op4 旬主星＝太陽/木星/水星（名望/擴張/契約）。',
    wealth:  'Sig 應落土堆（金幣）＝物質財富場域；Op2 應落 2/8 宮；Op4 旬主星＝金星/木星（財富擴張）。被問具體金額而落點無對應＝講形式不保證數字。',
    health:  'Sig 落水堆過多＝情緒影響身體；Op2 應落 6/8/12 宮；Op4 旬主星＝土星/火星（慢性/急症）。只給方向時段、不診斷病名。',
    spiritual:'★ Op5 生命之樹質點＝此題核心答案（其他題 Op5 是收束層，此題直接是答案）；五層花色集中度＝靈魂目前最強的能量類型。'
  };

  // ── 注入片段(治本式接全集，避免堆 prompt：只補既有 head 沒有的高槓桿規則)──
  // ① 嚴格不確定判準：防 AI 把「訊號弱」當偷懶藉口
  var FRAG_UNCERTAINTY_TAROT =
    '\n【說「訊號弱」前的硬檢查】\n「弱」不是偷懶的避難所。要說某題訊號弱，先逐項確認：每張牌位置意義、元素尊嚴、花色分布、對立牌組都對照過這題了。全部過、這盤真的沒指向，才能說弱，並寫出「看了哪幾項沒給」。禁止：沒看完就說弱、或只丟「不確定」三個字。\n';
  var FRAG_UNCERTAINTY_OOTK =
    '\n【說「訊號弱」前的硬檢查】\n「弱」不是偷懶的避難所。要說某層訊號弱，先逐項確認：該 Op 的 Sig 落點＋counting＋pairing＋dignities＋unaspected 五項都看過了。全部過、這層真的沒指向，才能說弱，並寫出「看了哪幾項沒給」。禁止：沒看完就說弱、或只丟「不確定」三個字。\n';
  // ② 學理鎖定：擋掉網紅/心理學/雞湯，逼回正統
  var FRAG_SOURCELOCK =
    '\n【學理鎖定】只用三種知識：①本盤實際數據 ②正統經典（Waite-Smith、Golden Dawn Book T、Mathers、Crowley、PHB）③通用邏輯（元素生剋、因果、數字階段）。禁止：現代網紅塔羅詮釋、把心理學框架硬套（「逆位＝陰影面需療癒」）、靈性雞湯（「宇宙在指引你」）、無牌面支撐的道德勸說（「你該放下執著」）。\n';
  // ③ 交稿前 recency 檢查：Sonnet 類模型最常在後半段破功，放最後一段（recency 最強）
  var FRAG_RECENCY_TAROT =
    '\n' + BAR + '\n交稿前檢查（後半段最容易破功）\n' + BAR +
    '\n□ 依牌陣類型讀對位置張數 □ 組合讀非逐張報義 □ 無粗體標題 □ 凶牌逆位沒包裝成正面 □ 每個問題正面回答 □ 只引用盤上的牌 □ 三層因果鏈至少走了 2 條 □ 數據層清單全覆蓋 □ 有 24h 動作＋可驗證信號 □ 能量石收尾\n';
  var FRAG_RECENCY_OOTK =
    '\n' + BAR + '\n交稿前檢查（後半段最容易破功）\n' + BAR +
    '\n□ 五個 Op 都給了 Counting＋Pairing＋Dignities＋Unaspected＋結論 □ 無粗體標題（禁「**表層：**」「**Op1：**」）□ 元素尊嚴至少 2 層有展開 □ Unaspected 每層都有點名或說明無 □ 跨層重複牌至少點出 2 組 □ 沒發明禁用說法（結論牌/答案牌/五層共振/同步度）□ 總答有依據且明說哪層權重最高 □ Op4 有具體日期錨 □ Op5 點出三角＋質點 □ 深層拆解從落點推非表層換說法 □ 結尾有可驗證信號 □ 能量石收尾\n';

  // ④ 收尾能量石（v73.1 歐那）：把賣場自然融進解讀結論，不是廣告區塊。
  //    元素一律綁「牌面」（火水風土＝權杖聖杯聖杯寶劍金幣／開鑰 Op1 YHVH 堆），
  //    不碰八字五行命盤——故塔羅與開鑰共用，且不破壞開鑰「不引命盤」的純粹性。
  //    放在 t.tail 之後、recency 檢查之前（緊貼結論、但不搶最後品管位）。
  var FRAG_CRYSTAL =
    '\n【收尾・能量石（自然帶出，禁推銷腔）】\n' +
    '整份解讀寫完後，用最後 2-3 句輕收一個尾：先判斷這次牌面「最關鍵的一個元素失衡」——是某元素缺得最明顯，還是某元素過盛在添亂——再對應一種可隨身的能量石，當成支持這股能量的小提醒，像解讀者順口補的一句，不是賣貨。元素對照（缺的補、過盛的衡，挑一種）：火（權杖）不足→紅瑪瑙/太陽石/紅碧玉；火過盛躁進→黑曜石/黑碧璽。水（聖杯）不足→粉晶/草莓晶/月光石；水過盛情緒淹沒→拉長石/海藍寶。風（寶劍）不足→白水晶/海藍寶/藍紋瑪瑙（添清明與溝通）；風過盛心緒亂→紫水晶/紫龍晶。土（金幣）不足→黃水晶/茶晶/虎眼石；土過盛僵固→綠幽靈/葡萄石。判斷以你這次的實際結論為準。若這次牌面以寶劍密集／塔／惡魔等衝突崩解為主，改建議天鐵（辟邪定志）或龍宮舍利（護身安神）這類防護材質。\n' +
    '鐵則：只挑一種、只講一次、要貼著這次的結論說清「為什麼對你是它」（不可泛泛而談），全程不得出現「優惠／特價／限時／下單／搶購／快買」任何字眼。講完後輕附一句去處即可：「想入手的話，我蝦皮〈靜月之光〉有水晶・天鐵・龍宮舍利 https://tw.shp.ee/9UHEJTp4」。若這次牌面沉重，語氣保持穩重，把石頭定位成「陪你穩住」，而不是趁機促購。\n';

  // ── 組裝「本次問題鎖定」區塊（放在提示詞最前面，primacy 最強）──
  //    ★ v70.4(歐那 2026/5/29)：分工具。塔羅快讀＝yes/no 直答導向；
  //      開鑰之法＝深度拆解導向（絕不能用塔羅的「給是非、禁止擴寫」框架，那會直接掐死開鑰的五層拆解本質）。
  function buildFocusLock(q, tool) {
    var f = detectFocus(q);
    var L = [];
    L.push(BAR);
    L.push('◆ 本次問題鎖定（最高優先，先讀這段再讀下面的技法）');
    L.push(BAR);
    try { var _Q = (typeof window !== 'undefined') && window.JY_QUERENT; if (_Q && (_Q.age || _Q.gender)) L.push('【問卜者本人】' + [(_Q.gender || ''), (_Q.age ? ('約' + _Q.age + '歲') : '')].filter(Boolean).join('、') + '——宮廷牌年齡段以此為基準推（問卜者年長＝騎士多為晚輩/下屬；年輕＝皇后國王多為長輩）。'); } catch (e) {}

    // ── 開鑰之法：深度拆解導向（不走塔羅的 yes/no 框架）──
    if (tool === 'ootk') {
      if (f.noQ) {
        L.push('問卜者沒有填寫明確問題。');
        L.push('→ 依五次操作的落點與 counting／pairing，從牌面反推這次最該被回答的核心議題，做完整的五層拆解；不要七個領域亂掃。');
        return L.join('\n') + '\n';
      }
      L.push('問卜者問的是：' + f.raw);
      var domZh = f.domains.length ? ({ love: '感情／情慾', career: '事業／工作', wealth: '財運', health: '健康', spiritual: '靈性' })[f.domains[0]] : '';
      if (domZh) {
        var fieldNote = (f.domains.indexOf('love') >= 0 && /同事|公司|主管|老闆|職場/.test(f.raw))
          ? '（對象來自工作場域——場域只是「在哪認識」，不改變這是情慾／感情題的本質）' : '';
        L.push('問題領域：' + domZh + fieldNote + '。');
      }
      if (f.domains.length && OOTK_ROUTING[f.domains[0]]) L.push('・落點對應判準：' + OOTK_ROUTING[f.domains[0]]);
      L.push('這是開鑰之法，不是塔羅快讀——不要停在表層「會／不會」就結束。開鑰的價值是拆穿問題背後真正在問什麼、卡在哪、重複過幾次、現在能做什麼：');
      L.push('・開頭可用一兩句點出表層傾向，但重點往下拆，務必完成「問題四層拆解」（表層／深層／重複模式／鑰匙行動，全部只從牌面落點推、不做性格診斷）。深層問題要從牌面推這題背後真正在問的——例如問「對方想不想」常是在問「我在這段關係裡的位置」「我是否渴望被這個人慾望」，不要套通則。');
      L.push('・Sig 落在任何元素堆／宮位／星座／質點，都讀成「揭示真實場域」、對準問題給洞察，不是失敗、不是 abandon。例：落水堆＝這件事對你是情感投射；落第 1 宮＝核心其實在你自己（你的吸引力、你的慾望、你怎麼看自己），不只在對方。');
      if (f.portrait || f.domains.indexOf('love') >= 0) {
        L.push('・問的是對方意圖／狀態→用第十一節五次操作內部交叉推「對方目前狀態＋兩人能量距離」，給得出畫像就給，不要只丟一個是非。');
      }
      return L.join('\n') + '\n';
    }

    // ── 塔羅快讀：yes/no 直答導向 ──
    if (f.noQ) {
      L.push('問卜者沒有填寫明確問題。');
      L.push('→ 不要七個領域都掃一遍。先看花色集中：聖杯多＝感情、權杖多＝事業/行動、金幣多＝財務、寶劍多＝思考/衝突；以最集中的花色鎖定「一個」最可能的領域，針對它給通盤，其餘略過。');
      return L.join('\n') + '\n';
    }

    L.push('問卜者問的是：' + f.raw);

    // 形態（決定回答骨架）
    var shape = [];
    if (f.yesno)    shape.push('是非題');
    if (f.decision) shape.push('決策題');
    if (f.timing)   shape.push('時間題');
    if (f.portrait) shape.push('對方畫像題');
    if (f.domains.length) shape.push(({love:'感情',career:'事業',wealth:'財運',health:'健康',spiritual:'靈性'})[f.domains[0]] + '題');
    if (shape.length) L.push('問題性質：' + shape.join('＋') + (f.urgent ? '＋極短時間窗' : '') + '。');

    L.push('回答要求：');
    L.push('・第一句就直接回答「' + f.raw + '」這個問題本身，不要鋪墊、不要先講方法。只圍繞這個問題答，禁止擴寫成「你們長期走向」「你的人生課題」這類通盤論述。');

    if (f.yesno)    L.push('・是非題：第一句給「會／不會／是／不是／不一定（但傾向X）」。逆位/凶牌不要硬讀成正面。');
    if (f.decision) L.push('・決策題：兩個選項各給支持證據，比完之後明確推一個，不要兩邊都好。');
    if (f.timing) {
      L.push('・時間題：必須給「為什麼是這個時間」。用花色速度收斂——權杖（火）最快、寶劍（風）次快但偏衝突、聖杯（水）中等、金幣（土）最慢；或用小牌 Decan 日期、大牌時間含義。要說出從哪張牌推。禁止「近期/快了/順其自然」這種沒錨點的話。');
      if (f.urgent) L.push('・這是「' + (/今晚/.test(f.raw) ? '今晚' : /今天/.test(f.raw) ? '今天' : '這一兩天') + '」的極短窗：直接收斂到當下能不能成。土系牌多＝慢/今晚不太可能；火系牌多＝當下就有動能。據此給明確傾向。');
    }
    if (f.portrait) L.push('・對方畫像題：先檢查牌陣有沒有宮廷牌——有才推年齡/外型，沒有就誠實說「牌面沒有人物牌，無法推年齡」，不要憑空編「她35歲/姓黃/金融業」。');

    f.domains.slice(0, 2).forEach(function (d) { if (DOMAIN_HINT[d]) L.push('・' + DOMAIN_HINT[d]); });

    return L.join('\n') + '\n';
  }

  // ── 取排盤資料塊（沿用現有 builder，已含全部 GD/Mathers/Crowley/PHB 運算）──
  // ── 防呆字串化：任何型別都轉成乾淨文字，杜絕 [object Object] ──
  function safeText(v) {
    if (v === null || v === undefined) return '';
    var t = typeof v;
    if (t === 'string' || t === 'number' || t === 'boolean') return String(v);
    if (Array.isArray(v)) {
      return v.map(function (item) {
        if (item === null || item === undefined) return '';
        var it = typeof item;
        if (it === 'string' || it === 'number' || it === 'boolean') return String(item);
        if (it === 'object') {
          if (item.meaning && (item.a || item.b)) return (item.a || '') + '↔' + (item.b || '') + '：' + item.meaning;
          if (item.message) return ((item.cards && item.cards.join) ? item.cards.join('×') + '——' : '') + item.message;
          if (item.name && item.meaning) return item.name + '：' + item.meaning;
          if (item.cardName) return item.cardName + (item.sephirotZh ? '→' + item.sephirotZh : '');
          return Object.keys(item).map(function (k) {
            var x = item[k];
            return (typeof x === 'string' || typeof x === 'number') ? String(x) : '';
          }).filter(Boolean).join(' ');
        }
        return '';
      }).filter(Boolean).join('；');
    }
    if (t === 'object') {
      if (v.meaning) return String(v.meaning);
      return Object.keys(v).map(function (k) {
        var s = safeText(v[k]);
        return s ? (k + '：' + s) : '';
      }).filter(Boolean).join('｜');
    }
    return '';
  }

  // ── 塔羅：結構化物件 → 模板要的逐張牌文字 + 預運算數據 ──
  function formatTarotData(result) {
    var td = (result && result.tarotData) || {};
    var cards = td.cards || [];
    var L = [];
    L.push('牌陣類型與各位置：');
    L.push((td.spreadZh || td.spreadType || '未指定牌陣') + '（共 ' + cards.length + ' 張）');
    L.push('');
    L.push('抽到的牌（逐張，含位置／牌名／正逆位）：');
    cards.forEach(function (c, i) {
      var pos = c.positionMeaning || c.position || ('位置' + (i + 1));
      var ln = (i + 1) + '. ' + pos + '：' + (c.name || '');
      if (c.keywords) ln += '〔' + c.keywords + '〕';
      if (c.meaning) ln += ' → ' + c.meaning;
      if (c.gdCourtAnalysis) { var g = safeText(c.gdCourtAnalysis); if (g) ln += '｜宮廷牌：' + g; }
      L.push(ln);
    });
    // ★ 合法牌名清單（禁幻覺——複製模式沒有後端機械審計，用清單替代）
    var _legal = cards.map(function (c) { return c.name; }).filter(Boolean);
    if (_legal.length) { L.push(''); L.push('【本次合法牌名清單（你只能引用這些牌，清單外的牌一律不可出現）】'); L.push('  ' + _legal.join('、')); }
    var extra = [];
    function add(label, val) { if (val !== null && val !== undefined && val !== '' && !(val.length === 0)) { var s = safeText(val); if (s) extra.push(label + '：' + s); } }
    add('正逆比與基調', td.summary);
    add('大牌比重', td.majorWeight);
    add('元素尊嚴', td.elementalDignity);
    add('元素互動', td.elementInteraction);
    add('對立牌組', td.opposingPairs);
    add('故事弧線', td.storyArc);
    add('特殊牌組合', td.combos);
    add('數字模式', td.numberPatterns);
    add('時間推斷', td.timeConclusion);
    add('宮廷人物', td.courtPeople);
    add('數字學', td.numerology);
    add('卡巴拉對應', td.kabbalah);
    add('牌面張力', td.tensions);
    if (td.preStats && td.preStats.insights) add('前端洞察', td.preStats.insights);
    if (extra.length) {
      L.push('');
      L.push('── 以下數據已由排盤系統精算完成，請【直接採用】，不要自行重算。尤其元素尊嚴、Decan 日期、數字模式是機械運算結果，重算只會出錯——你的工作是「解讀」這些既定數據，不是重算 ──');
      extra.forEach(function (e) { L.push('・' + e); });
    }
    return L.join('\n');
  }

  // ── 開鑰之法：結構化物件 → 五次操作完整文字 + 補充觀察 ──
  function formatOOTKData(result) {
    var od = (result && result.ootkData) || {};
    var ops = od.operations || {};
    var ca = od.crossAnalysis || {};
    var sig = od.significator || {};
    var L = [];
    L.push('── 以下五次操作資料（含 counting／pairing／dignities 計算）已由排盤系統精算完成，請【直接採用】。計數路徑、配對、元素尊嚴都已算好——不要自己重數、重排或重算位置（Book T counting 自己算極易出錯）；你的工作是依這些既定結果做解讀。【計數值】本引擎 Aces 採 count 11（Crowley·Liber 78）；如 Paul Hughes-Barlow 慣例，你可另提 count 5（GD：四元素＋乙太）的分支作對照，但上述路徑是 11 版，不要拿 5 去否定它 ──');
    L.push('');
    var sigName = sig.n || sig.name || (typeof sig === 'string' ? sig : '');
    var sigPrefix = (sig.isUp === true) ? '▲正位 ' : (sig.isUp === false ? '▼逆位 ' : '');
    var sigFacing = (sig.isUp === true) ? '（正位→面向右，counting 向右走）' : (sig.isUp === false ? '（逆位→面向左，counting 向左走）' : '');
    L.push('代表牌（Significator）：' + (sigName ? (sigPrefix + sigName + sigFacing) : (safeText(sig) || '（未提供）')));
    if (ca.significatorDirectional && ca.significatorDirectional.meaning) L.push('面向解讀：' + ca.significatorDirectional.meaning);
    L.push('');
    var opLabels = {
      op1: 'Op1 四元素堆（YHVH）——當下處境',
      op2: 'Op2 十二宮——問題的展開',
      op3: 'Op3 十二星座——進一步展開（內在驅力）',
      op4: 'Op4 三十六旬——倒數階段（時機節奏）',
      op5: 'Op5 生命之樹——最終結果（靈魂本質）'
    };
    // ★ 把每層真正結構化（鏡像 head 要的 Sig落點/Counting/Pairing/Dignities），
    //   op-specific 欄位（宮/星座/旬/質點，名稱不一）用 safeText 保底，絕不漏資料。
    function cn(c) { return c ? ((c.n || c.name || '?') + (c.isUp === false ? '逆' : '')) : '?'; }
    var PILE = { fire: '火堆 Yod（工作/事業）', water: '水堆 Heh（感情/快樂）', air: '風堆 Vau（衝突/損失/思維）', earth: '土堆 Heh-final（金錢/物質）' };
    // ── 精簡：引擎把整個 Op 分析當長字串傳來；剝掉每層重複 5 次的方法論講稿＋Mathers 1888 字典＋裝飾線，只留實際牌面/路徑/配對/落點 ──
    function _slimOp(s) {
      if (typeof s !== 'string') return safeText(s);
      return s
        // ── 額外鷹架（每 Op 重複、且 head 已涵蓋，砍掉不損牌義／路徑／尊嚴）──
        .replace(/[^\n]*盤面揭示・真實場域（[^）]*）/g, '')
        .replace(/【本 Op Narrative Pairs[\s\S]*?(?=\n+[━【★]|$)/g, '')
        .replace(/（配對是從代表牌兩側對稱展開[\s\S]*?）/g, '')
        .replace(/【Op4 雙版本對照】[\s\S]*?鏡像細節」。/g, '')
        .replace(/（Mathers：[^（）]*）/g, '')
        // ── v74.1 清理：截斷的方向尊嚴 JSON、孤兒 📍、與 Thoth 鎖矛盾的 Waite 挑版本提示 ──
        .replace(/【本 Op 宮廷牌面向互動[\s\S]*$/g, '')
        .replace(/\n[ \t]*\u{1F4CD}[ \t]*(?=\n)/gu, '')
        .replace(/★ AI 提示:Waite 與 Crowley[\s\S]*?並列。/g, '')
        // ── v74.2：Op4 同數字組／主導花色 砂 Waite 欄，只留 Crowley（呼應 head 的 Thoth 鎖）──
        .replace(/\n[ \t]*\[Waite 1910\][^\n]*/g, '')
        .replace(/Mathers 1888:[^\/\n]*\/ *(?=Crowley Liber 78)/g, '')
        .replace(/Waite 1910 vs /g, '')
        .replace(/ 雙版本/g, '')
        .replace(/（v63 正統 Book T：[\s\S]*?結論牌」。）/g, '')
        .replace(/（★ Mathers 原文：[\s\S]*?本身的能量。）/g, '')
        .replace(/★ 本 Op 為[\s\S]*?排名。/g, '')
        .replace(/Mathers 原文:「Pair[\s\S]*?不跨 Op 比較。/g, '')
        .replace(/Sig\([^)]*\)兩次都落[\s\S]*?照常進行。/g, '')
        .replace(/AI 必讀:[\s\S]*?(?:照常給答案。|當讀盤失敗。|不可給強斷言。)/g, '')
        .replace(/Op\d+ 預期觀察[\s\S]*?分開看。」/g, '')
        .replace(/把這個落堆讀成[\s\S]*?照常給答案。/g, '')
        .replace(/不要寫「[^」]*」「?[^」]*」?，?也?不要把它當讀盤失敗。/g, '')
        .replace(/【Mathers 1888:[^】]*】/g, '')
        .replace(/【GD:[^】]*】/g, '')
        .replace(/【(?:well|ill) 含義:[^】]*】/g, '')
        .replace(/[━]{6,}/g, '')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
    }
    ['op1', 'op2', 'op3', 'op4', 'op5'].forEach(function (k) {
      if (!ops[k]) return;
      var o = ops[k];
      L.push('────────────────────────');
      L.push('【' + opLabels[k] + '】');
      if (typeof o === 'string') { var _s = _slimOp(o); if (_s) L.push(_s); L.push(''); return; }
      if (o.activePile) L.push('Sig 落點/活躍堆：' + (PILE[o.activePile] || o.activePile) + (o.meaning ? '（' + o.meaning + '）' : ''));
      if (o.activeCards && o.activeCards.length) L.push('本層活躍牌：' + o.activeCards.map(cn).join('、'));
      if (o.countingPath && o.countingPath.length) L.push('Counting 走過（依序，方向已定，勿重數）：' + o.countingPath.map(function (p) { return (p.cardName || '?') + (p.isUp === false ? '逆' : '') + '〔走' + p.countValue + '〕'; }).join(' → '));
      if (o.mq_countingPath && o.mq_countingPath.length) L.push('Op4 環形 Counting（順發牌方向，1↔36 時序）：' + o.mq_countingPath.map(function (p) { return (p.cardName || '?') + '〔走' + p.countValue + '〕'; }).join(' → '));
      if (o.pairs && o.pairs.length) L.push('Pairing 配對（Sig 兩側往外，#1 最直接）：' + o.pairs.map(function (pr, i) { if (!pr || pr.single || !pr.right) return '#' + (i + 1) + ' 單張殘餘:' + cn(pr && pr.left); return '#' + (i + 1) + ' ' + cn(pr.left) + '↔' + cn(pr.right) + (pr.dignity ? '〔' + safeText(pr.dignity) + '〕' : ''); }).join('；'));
      if (o.dignities) { var _dg = safeText(o.dignities); if (_dg) L.push('元素尊嚴：' + _dg); }
      // op-specific 落點與其餘欄位（宮位/星座/旬/質點及其含義，名稱不一，safeText 保底）
      var _rest = {}, _skip = { piles: 1, activePile: 1, meaning: 1, activeCards: 1, sigIndex: 1, keyCards: 1, countingPath: 1, mq_countingPath: 1, pairs: 1, dignities: 1 };
      Object.keys(o).forEach(function (kk) { if (!_skip[kk]) _rest[kk] = o[kk]; });
      var _rs = safeText(_rest); if (_rs) L.push('本層落點與其他：' + _rs);
      L.push('');
    });
    var obs = [];
    function addO(label, val) { if (val !== null && val !== undefined && val !== '' && !(val.length === 0)) { var s = safeText(val); if (s) obs.push(label + '：' + s); } }
    addO('Unaspected Cards（各層隱藏推力，單層內判斷）', ca.unaspectedCards);
    addO('宮廷人物', od.courtPeople);
    addO('大牌比重', od.majorWeight);
    addO('數字模式', od.numberPatterns);
    addO('可解度閘門', ca.divinationValidity || od.divinationValidity);
    if (obs.length) {
      L.push('── 五次操作的補充觀察數據（皆為單層內判斷，勿跨層彙整成綜合訊號）──');
      obs.forEach(function (o) { L.push('・' + o); });
    }
    // ★ 合法牌名清單（禁幻覺）— 盡力從各 Op 蒐集實際出現的牌名
    try {
      var _seen = {};
      (function harvest(o) {
        if (!o) return;
        if (Array.isArray(o)) { o.forEach(harvest); return; }
        if (typeof o === 'string') {
          // 字串型 op（引擎預格式化）：用正則抽「正位/逆位 <牌名>」
          var _re = /[正逆]位\s+([^\s〔（()→↔，。\n]{2,5})/g, _m;
          while ((_m = _re.exec(o))) { _seen[_m[1]] = 1; }
          return;
        }
        if (typeof o === 'object') {
          var nm = o.n || o.name || o.cardStr || o.cardName;
          if (nm && typeof nm === 'string' && nm.length <= 8) _seen[nm] = 1;
          Object.keys(o).forEach(function (k) { harvest(o[k]); });
        }
      })(ops);
      if (sigName) _seen[sigName] = 1;
      var _lg = Object.keys(_seen);
      L.push('');
      if (_lg.length > 1) { L.push('【本次合法牌名清單（只能引用本盤實際出現的牌，清單外一律不可出現）】'); L.push('  ' + _lg.join('、')); }
      else { L.push('【禁幻覺】只能引用本盤五次操作資料區實際出現的牌（活躍堆／counting／pairing 列出的都算），清單外的牌一律不可出現。'); }
    } catch (e) { L.push(''); L.push('【禁幻覺】只能引用本盤資料區實際出現的牌，清單外的牌一律不可出現。'); }
    return L.join('\n');
  }

  // ── 取排盤資料塊（沿用現有 builder，已含全部 GD/Mathers/Crowley/PHB 運算）──
  function getPayload(tool) {
    try {
      var obj = null;
      // ★ 修：builder 是頂層 function 宣告，裸名優先、window 後備、最後 eval 取全域
      function _callBuilder(name) {
        try { var fn = (0, eval)('typeof ' + name + ' === "function" ? ' + name + ' : null'); if (fn) return fn(); } catch (e) {}
        try { if (typeof window !== 'undefined' && typeof window[name] === 'function') return window[name](); } catch (e) {}
        return null;
      }
      if (tool === 'ootk') {
        obj = _callBuilder('_buildOOTKPayload');
      } else {
        obj = _callBuilder('_buildTarotOnlyPayload');
      }
      if (!obj) return '（找不到排盤資料，請先完成抽牌／排盤）';
      if (typeof obj === 'string') return obj; // 防呆：萬一回傳字串
      if (obj.mode === 'ootk' || obj.ootkData) return formatOOTKData(obj);
      return formatTarotData(obj);
    } catch (e) {
      return '（排盤資料組裝失敗：' + (e && e.message ? e.message : e) + '）';
    }
  }

  // ── 組成完整可複製提示詞 ──
  function buildPrompt(tool) {
    var t = TPL[tool];
    if (!t) return '';
    var question = getQuestion();
    var payload = getPayload(tool);
    var focusLock = buildFocusLock(question, tool); // ★ v70.4：分工具——開鑰走深度拆解、塔羅走 yes/no 直答
    return [
      focusLock,
      FRAG_SOURCELOCK,
      t.head.replace('{{SPREAD_READING_METHOD}}', (tool === 'tarot' ? getSpreadMethod() : '')),
      (tool === 'ootk' ? FRAG_UNCERTAINTY_OOTK : FRAG_UNCERTAINTY_TAROT),
      '',
      BAR,
      t.dataHeader,
      BAR,
      '',
      '問卜者的問題：',
      question,
      '',
      payload,
      '',
      t.tail,
      FRAG_CRYSTAL,
      (tool === 'ootk' ? FRAG_RECENCY_OOTK : FRAG_RECENCY_TAROT)
    ].join('\n');
  }
  window.JY_buildExportPrompt = buildPrompt;

  // ── 複製到剪貼簿（含 fallback）──
  function copyText(text, btn) {
    function done(ok) {
      if (!btn) return;
      var old = btn.getAttribute('data-old') || btn.textContent;
      btn.setAttribute('data-old', old);
      btn.textContent = ok ? '✓ 已複製到剪貼簿' : '✗ 複製失敗，請手動全選';
      btn.disabled = false;
      setTimeout(function () { btn.textContent = old; }, 2200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;top:0;';
        document.body.appendChild(ta);
        ta.focus(); ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        done(ok);
      } catch (e) { done(false); }
    }
  }

  // ── 渲染複製 UI 到指定容器 ──
  //    mount 可為 DOM element 或 id 字串；tool = 'tarot' | 'ootk'
  // ── 一次性注入儀式卡樣式 ──
  function ensureFx() {
    if (document.getElementById('jy-export-fx')) return;
    var st = document.createElement('style');
    st.id = 'jy-export-fx';
    st.textContent = [
      '@keyframes jyExHalo{0%,100%{opacity:.5;transform:scale(1)}50%{opacity:.95;transform:scale(1.08)}}',
      '@keyframes jyExStar{0%{transform:translateY(0) scale(1);opacity:0}15%{opacity:.9}85%{opacity:.7}100%{transform:translateY(-46px) scale(.4);opacity:0}}',
      '@keyframes jyExSheen{0%{transform:translateX(-130%)}60%,100%{transform:translateX(130%)}}',
      '@keyframes jyExRise{0%{opacity:0;transform:translateY(14px)}100%{opacity:1;transform:translateY(0)}}',
      '@keyframes jyExRing{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}',
      '.jy-ex-card{position:relative;overflow:hidden;max-width:560px;margin:1.1rem auto;padding:2.1rem 1.5rem 1.7rem;border-radius:22px;',
        'background:radial-gradient(135% 120% at 50% -10%,rgba(60,42,12,.55),rgba(16,12,8,.96) 62%);',
        'border:1px solid rgba(212,175,55,.32);box-shadow:0 18px 50px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,236,184,.12);',
        'animation:jyExRise .6s ease-out both}',
      '.jy-ex-card::before{content:"";position:absolute;top:-90px;left:50%;width:280px;height:280px;margin-left:-140px;border-radius:50%;',
        'background:radial-gradient(circle,rgba(233,207,110,.28),rgba(233,207,110,0) 70%);animation:jyExHalo 5s ease-in-out infinite;pointer-events:none}',
      '.jy-ex-stars{position:absolute;inset:0;pointer-events:none;overflow:hidden}',
      '.jy-ex-stars i{position:absolute;bottom:18%;width:3px;height:3px;border-radius:50%;background:rgba(255,236,184,.9);box-shadow:0 0 6px rgba(255,236,184,.7);animation:jyExStar linear infinite}',
      '.jy-ex-emblem{position:relative;width:62px;height:62px;margin:0 auto .9rem;display:flex;align-items:center;justify-content:center;font-size:1.7rem;z-index:1}',
      '.jy-ex-emblem::after{content:"";position:absolute;inset:-7px;border-radius:50%;border:1px dashed rgba(212,175,55,.45);animation:jyExRing 18s linear infinite}',
      '.jy-ex-title{position:relative;z-index:1;text-align:center;font-family:var(--f-display,"Noto Serif TC",serif);font-size:1.16rem;font-weight:700;letter-spacing:.04em;color:#f0d98a;margin-bottom:.5rem;text-shadow:0 2px 14px rgba(0,0,0,.6)}',
      '.jy-ex-sub{position:relative;z-index:1;text-align:center;font-size:.82rem;line-height:1.78;color:rgba(232,220,200,.72);max-width:430px;margin:0 auto 1.4rem}',
      '.jy-ex-sub b{color:#e9cf6e;font-weight:600}',
      '.jy-ex-btn{position:relative;z-index:1;display:block;width:100%;max-width:340px;margin:0 auto;padding:1rem 1.2rem;border:none;border-radius:14px;cursor:pointer;',
        'font-family:inherit;font-size:1rem;font-weight:800;letter-spacing:.05em;color:#231406;overflow:hidden;',
        'background:linear-gradient(135deg,#f6e29a 0%,#e3c25e 45%,#c9a23f 100%);',
        'box-shadow:0 10px 30px rgba(201,162,63,.4),inset 0 1px 0 rgba(255,255,255,.5);transition:transform .15s,box-shadow .2s}',
      '.jy-ex-btn:hover{transform:translateY(-2px);box-shadow:0 14px 38px rgba(201,162,63,.55),inset 0 1px 0 rgba(255,255,255,.6)}',
      '.jy-ex-btn:active{transform:translateY(0)}',
      '.jy-ex-btn::after{content:"";position:absolute;top:0;left:0;width:55%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.7),transparent);transform:translateX(-130%);animation:jyExSheen 3.6s ease-in-out infinite}',
      '.jy-ex-foot{position:relative;z-index:1;text-align:center;font-size:.7rem;color:rgba(212,175,55,.5);margin-top:.95rem;letter-spacing:.03em}'
    ].join('');
    document.head.appendChild(st);
  }

  function starsHTML() {
    var lefts = [8, 20, 33, 46, 58, 70, 82, 92, 27, 64];
    var s = '';
    for (var i = 0; i < lefts.length; i++) {
      var dur = (3.4 + (i % 5) * 0.6).toFixed(1);
      var delay = ((i * 0.5) % 4).toFixed(1);
      var sz = (i % 3 === 0) ? 4 : 3;
      s += '<i style="left:' + lefts[i] + '%;width:' + sz + 'px;height:' + sz + 'px;animation-duration:' + dur + 's;animation-delay:' + delay + 's"></i>';
    }
    return s;
  }

  // ── 渲染華麗儀式複製卡（不顯示提示詞文字，只留複製按鈕＋說明）──
  function render(tool, mount) {
    var el = (typeof mount === 'string') ? document.getElementById(mount) : mount;
    if (!el) { console.warn('[prompt-export] 找不到掛載容器'); return; }
    ensureFx();
    var t = TPL[tool] || { label: '命理' };
    var prompt = buildPrompt(tool);
    var emblem = (tool === 'ootk') ? '🗝️' : '🔮';

    var card = document.createElement('div');
    card.className = 'jy-ex-card';
    card.innerHTML =
      '<div class="jy-ex-stars">' + starsHTML() + '</div>' +
      '<div class="jy-ex-emblem">' + emblem + '</div>' +
      '<div class="jy-ex-title">' + t.label + '・占卜提示詞已備妥</div>' +
      '<div class="jy-ex-sub">輕觸下方按鈕複製，貼到任何 AI 對話（<b>ChatGPT・Claude・Gemini・Grok</b>）送出，' +
        '即可得到一份完整深入的命理解讀。<br>提示詞已封入全套占卜技法與你此刻的牌面，無需再多做說明。</div>' +
      '<button type="button" class="jy-ex-btn">✦ 一鍵複製占卜提示詞 ✦</button>' +
      '<div class="jy-ex-foot">複製後貼上送出即可 ・ 完全免費</div>';

    var btn = card.querySelector('.jy-ex-btn');
    btn.addEventListener('click', function () { copyText(prompt, btn); });

    el.innerHTML = '';
    el.appendChild(card);
    try { window.scrollTo({ top: 0, behavior: 'instant' }); } catch (e) {}
  }
  window.JY_renderExportPrompt = render;

  // ════════════════════════════════════════
  //  複製模式接線（無 worker / 無 API / 無付費）
  //  注意：prompt-export.js 必須在 tarot.js 之後載入，OOTK 覆寫才會生效
  // ════════════════════════════════════════

  // 塔羅複製入口：由 ai-analysis.js 的 _triggerTarotAI 早退呼叫
  window._jyTarotCopyMode = function () {
    var w = document.getElementById('tarot-ai-wrap');
    if (!w) return;
    w.style.display = '';
    try { var ow = document.getElementById('ootk-ai-wrap'); if (ow) ow.style.display = 'none'; } catch (e) {}
    try { window._jyActiveResultMode = 'tarot'; } catch (e) {}
    try { window._jyResultModes = window._jyResultModes || {}; window._jyResultModes.tarot = true; if (typeof _refreshAllNavs === 'function') _refreshAllNavs('tarot'); } catch (e) {}
    render('tarot', w);
  };

  // 開鑰之法：複製模式由 tarot_upgrade.js 的 _triggerOOTKAI 源頭早退處理
  //   （該函數會在抽完牌後直接呼叫 JY_renderExportPrompt('ootk', wrap)）
  //   此處不再覆寫 window._ootkTriggerAI——因為 startOOTK 每次抽牌會動態重設它，覆寫擋不住。

})();
