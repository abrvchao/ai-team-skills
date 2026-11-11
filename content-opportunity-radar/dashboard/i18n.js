"use strict";
(() => {
const en={
brandEyebrow:"Signal Intelligence Engine",workspaceLabel:"Workspace",legacyWorkspace:"Legacy / unscoped",scopeLabel:"Radar scope",languageLabel:"Language",viewResults:"View results",runScan:"Run Lite Scan",ready:"Ready",
heroEyebrow:"Top Opportunities Today",heroTitle:"Discover what's worth doing before deciding how to do it.",loadingHistory:"Loading Radar history…",metricOpportunities:"Opportunities",metricProviders:"Providers",metricLastRun:"Last run",
priorityQueue:"Priority queue",topOpportunities:"Top Opportunities",loading:"Loading",emptyTitle:"No opportunities to show yet.",emptyHint:"Run Radar Lite first, then return here to inspect the results.",
acquisitionHealth:"Acquisition health",dataProviders:"Data Providers",detailLabel:"Opportunity detail",close:"Close",scoreBreakdown:"Score breakdown",whyNow:"Why now",evidence:"Evidence",sourceConfirmation:"Source confirmation",history:"History",researchPack:"Research Pack",
aiTeamHandoff:"AI Team Handoff",generateHandoff:"Generate HANDOFF.json",handoffPrompt:"Select an opportunity and generate a deterministic handoff pack.",noSavedResults:"Scope “{scope}” has no saved Radar results.",
candidates:"candidates",deepScans:"deep scans",seed:"seed",updated:"updated",statusReady:"Ready",statusEmpty:"Empty",rankScore:"rank score",opportunity:"Opportunity",confidence:"Confidence",freshness:"Freshness",evidenceCount:"{count} evidence",
providerNoState:"No persistent collector job state yet.",events:"Events",next:"Next",last:"Last",success:"Success",componentDemand:"Demand",componentMomentum:"Momentum",componentSupplyGap:"Supply gap",componentAuthorityFit:"Authority fit",componentBusinessFit:"Business fit",
noSourceSummary:"No source summary available.",noHistory:"No history yet.",historyAria:"Opportunity rank score history",snapshots:"snapshots",noEvidenceDetails:"Evidence details are not available for this snapshot.",unknown:"unknown",openSource:"Open source ↗",
citations:"Citations",questions:"Questions",painRequests:"Pain / requests",evidenceGaps:"Evidence gaps",actionCard:"Action Card",firstStep:"First step",minimumOutput:"Minimum output",starterRisk:"Starter risk",stopRule:"Stop rule",
loadingOpportunity:"Loading…",rankLine:"Rank #{rank} · {scope}",detailSubtitle:"Opportunity {opportunity} · Rank score {rank} · Confidence {confidence}",noExplanation:"No explanation recorded.",showingEvidence:"showing {shown} of {total}",
detailLoadFailed:"Opportunity detail failed: {message}",unableLoad:"Unable to load opportunity",radarLoadFailed:"Radar load failed: {message}",statusError:"Error",scanning:"Scanning sources for the selected language…",completed:"Completed",scanFailed:"Scan failed",
scanResult:"{status} · {language}/{country} · {count} opportunities",handoffReady:"Ready to generate a deterministic AI Team handoff pack.",handoffGenerating:"Generating handoff…",handoffCreated:"Created",handoffFailed:"Handoff failed",openOpportunityFirst:"Open an opportunity first.",
scanFailedMessage:"Radar Lite scan failed: {message}",handoffFailedMessage:"AI Team handoff failed: {message}",statusHealthy:"healthy",statusStored:"stored",statusDegraded:"degraded",statusStale:"stale",statusRateLimited:"rate limited",statusAuthRequired:"auth required",statusFailed:"failed",statusDisabled:"disabled"
};
const zh={
brandEyebrow:"信号情报引擎",workspaceLabel:"工作区",legacyWorkspace:"旧数据 / 未分工作区",scopeLabel:"雷达范围",languageLabel:"语言",viewResults:"查看结果",runScan:"运行精简扫描",ready:"就绪",
heroEyebrow:"今日优先机会",heroTitle:"先发现值得做的事，再决定怎么做。",loadingHistory:"正在读取 Radar 历史结果…",metricOpportunities:"机会",metricProviders:"数据源",metricLastRun:"最近运行",
priorityQueue:"优先队列",topOpportunities:"优先机会",loading:"加载中",emptyTitle:"还没有可展示的机会。",emptyHint:"先运行一次 Radar Lite，再回来看结果。",
acquisitionHealth:"采集健康度",dataProviders:"数据源",detailLabel:"机会详情",close:"关闭",scoreBreakdown:"评分拆解",whyNow:"为什么是现在",evidence:"证据",sourceConfirmation:"来源确认",history:"历史变化",researchPack:"研究包",
aiTeamHandoff:"交给 AI Team",generateHandoff:"生成 HANDOFF.json",handoffPrompt:"选择一个机会，生成确定性的 AI Team 交接包。",noSavedResults:"范围“{scope}”暂无持久化 Radar 结果。",
candidates:"候选",deepScans:"深度扫描",seed:"种子",updated:"更新于",statusReady:"就绪",statusEmpty:"空",rankScore:"排名分",opportunity:"机会分",confidence:"置信度",freshness:"新鲜度",evidenceCount:"{count} 条证据",
providerNoState:"暂无持久化采集任务状态。",events:"事件",next:"下次",last:"最近尝试",success:"最近成功",componentDemand:"需求",componentMomentum:"动量",componentSupplyGap:"供给缺口",componentAuthorityFit:"权威匹配",componentBusinessFit:"商业匹配",
noSourceSummary:"暂无来源汇总。",noHistory:"暂无历史数据。",historyAria:"机会排名分历史",snapshots:"个快照",noEvidenceDetails:"当前快照暂无可展示的证据详情。",unknown:"未知",openSource:"打开来源 ↗",
citations:"引用",questions:"问题",painRequests:"痛点 / 请求",evidenceGaps:"证据缺口",actionCard:"行动卡",firstStep:"第一步",minimumOutput:"最小成品",starterRisk:"起步风险",stopRule:"停止条件",
loadingOpportunity:"正在加载…",rankLine:"排名 #{rank} · {scope}",detailSubtitle:"机会分 {opportunity} · 排名分 {rank} · 置信度 {confidence}",noExplanation:"暂无解释。",showingEvidence:"显示 {shown} / {total}",
detailLoadFailed:"机会详情加载失败：{message}",unableLoad:"无法加载机会",radarLoadFailed:"Radar 加载失败：{message}",statusError:"错误",scanning:"正在扫描所选语言的数据源…",completed:"扫描完成",scanFailed:"扫描失败",
scanResult:"{status} · {language}/{country} · {count} 个机会",handoffReady:"可生成确定性的 AI Team 交接包。",handoffGenerating:"正在生成交接包…",handoffCreated:"已生成",handoffFailed:"交接失败",openOpportunityFirst:"请先打开一个机会。",
scanFailedMessage:"Radar Lite 扫描失败：{message}",handoffFailedMessage:"AI Team 交接失败：{message}",statusHealthy:"健康",statusStored:"已存储",statusDegraded:"降级",statusStale:"过期",statusRateLimited:"限流",statusAuthRequired:"需要授权",statusFailed:"失败",statusDisabled:"已禁用"
};
const ja={
brandEyebrow:"シグナル・インテリジェンス・エンジン",workspaceLabel:"ワークスペース",legacyWorkspace:"旧データ / 未分類",scopeLabel:"レーダー範囲",languageLabel:"言語",viewResults:"結果を表示",runScan:"Lite スキャンを実行",ready:"準備完了",
heroEyebrow:"今日の優先機会",heroTitle:"取り組む価値のあることを見つけてから、実行方法を決めよう。",loadingHistory:"Radar の履歴を読み込み中…",metricOpportunities:"機会",metricProviders:"データソース",metricLastRun:"最終実行",
priorityQueue:"優先キュー",topOpportunities:"優先機会",loading:"読み込み中",emptyTitle:"表示できる機会はまだありません。",emptyHint:"まず Radar Lite を実行してから結果を確認してください。",
acquisitionHealth:"取得状態",dataProviders:"データソース",detailLabel:"機会の詳細",close:"閉じる",scoreBreakdown:"スコア内訳",whyNow:"なぜ今か",evidence:"根拠",sourceConfirmation:"ソース確認",history:"履歴",researchPack:"リサーチパック",
aiTeamHandoff:"AI Team への引き継ぎ",generateHandoff:"HANDOFF.json を生成",handoffPrompt:"機会を選び、決定的な引き継ぎパックを生成します。",noSavedResults:"範囲「{scope}」には保存済みの Radar 結果がありません。",
candidates:"候補",deepScans:"詳細スキャン",seed:"シード",updated:"更新",statusReady:"準備完了",statusEmpty:"空",rankScore:"順位スコア",opportunity:"機会スコア",confidence:"信頼度",freshness:"新鮮度",evidenceCount:"根拠 {count} 件",
providerNoState:"永続化された収集ジョブ状態はまだありません。",events:"イベント",next:"次回",last:"最終試行",success:"最終成功",componentDemand:"需要",componentMomentum:"勢い",componentSupplyGap:"供給ギャップ",componentAuthorityFit:"権威性適合",componentBusinessFit:"ビジネス適合",
noSourceSummary:"ソース概要はありません。",noHistory:"履歴はまだありません。",historyAria:"機会順位スコアの履歴",snapshots:"スナップショット",noEvidenceDetails:"このスナップショットには根拠の詳細がありません。",unknown:"不明",openSource:"元ソースを開く ↗",
citations:"引用",questions:"質問",painRequests:"課題 / 要望",evidenceGaps:"根拠の不足",actionCard:"アクションカード",firstStep:"最初の一歩",minimumOutput:"最小成果物",starterRisk:"開始時リスク",stopRule:"停止条件",
loadingOpportunity:"読み込み中…",rankLine:"順位 #{rank} · {scope}",detailSubtitle:"機会 {opportunity} · 順位スコア {rank} · 信頼度 {confidence}",noExplanation:"説明は記録されていません。",showingEvidence:"{total} 件中 {shown} 件を表示",
detailLoadFailed:"機会詳細の読み込みに失敗：{message}",unableLoad:"機会を読み込めません",radarLoadFailed:"Radar の読み込みに失敗：{message}",statusError:"エラー",scanning:"選択した言語のソースをスキャン中…",completed:"完了",scanFailed:"スキャン失敗",
scanResult:"{status} · {language}/{country} · 機会 {count} 件",handoffReady:"AI Team 用の決定的な引き継ぎパックを生成できます。",handoffGenerating:"引き継ぎを生成中…",handoffCreated:"生成済み",handoffFailed:"引き継ぎ失敗",openOpportunityFirst:"先に機会を開いてください。",
scanFailedMessage:"Radar Lite スキャン失敗：{message}",handoffFailedMessage:"AI Team 引き継ぎ失敗：{message}",statusHealthy:"正常",statusStored:"保存済み",statusDegraded:"低下",statusStale:"古い",statusRateLimited:"レート制限",statusAuthRequired:"認証が必要",statusFailed:"失敗",statusDisabled:"無効"
};
const ko={
brandEyebrow:"시그널 인텔리전스 엔진",workspaceLabel:"워크스페이스",legacyWorkspace:"기존 데이터 / 미분류",scopeLabel:"레이더 범위",languageLabel:"언어",viewResults:"결과 보기",runScan:"Lite 스캔 실행",ready:"준비됨",
heroEyebrow:"오늘의 우선 기회",heroTitle:"할 가치가 있는 일을 먼저 발견하고, 어떻게 실행할지 결정하세요.",loadingHistory:"Radar 기록을 불러오는 중…",metricOpportunities:"기회",metricProviders:"데이터 소스",metricLastRun:"최근 실행",
priorityQueue:"우선순위 큐",topOpportunities:"우선 기회",loading:"불러오는 중",emptyTitle:"표시할 기회가 아직 없습니다.",emptyHint:"먼저 Radar Lite를 실행한 뒤 결과를 확인하세요.",
acquisitionHealth:"수집 상태",dataProviders:"데이터 소스",detailLabel:"기회 상세",close:"닫기",scoreBreakdown:"점수 구성",whyNow:"왜 지금인가",evidence:"근거",sourceConfirmation:"출처 확인",history:"기록",researchPack:"리서치 팩",
aiTeamHandoff:"AI Team 핸드오프",generateHandoff:"HANDOFF.json 생성",handoffPrompt:"기회를 선택해 결정적 핸드오프 패키지를 생성합니다.",noSavedResults:"범위 “{scope}”에 저장된 Radar 결과가 없습니다.",
candidates:"후보",deepScans:"심층 스캔",seed:"시드",updated:"업데이트",statusReady:"준비됨",statusEmpty:"비어 있음",rankScore:"순위 점수",opportunity:"기회 점수",confidence:"신뢰도",freshness:"신선도",evidenceCount:"근거 {count}개",
providerNoState:"저장된 수집 작업 상태가 아직 없습니다.",events:"이벤트",next:"다음",last:"최근 시도",success:"최근 성공",componentDemand:"수요",componentMomentum:"모멘텀",componentSupplyGap:"공급 공백",componentAuthorityFit:"권위 적합도",componentBusinessFit:"비즈니스 적합도",
noSourceSummary:"출처 요약이 없습니다.",noHistory:"아직 기록이 없습니다.",historyAria:"기회 순위 점수 기록",snapshots:"스냅샷",noEvidenceDetails:"이 스냅샷에는 근거 상세가 없습니다.",unknown:"알 수 없음",openSource:"원문 열기 ↗",
citations:"인용",questions:"질문",painRequests:"문제 / 요청",evidenceGaps:"근거 공백",actionCard:"액션 카드",firstStep:"첫 단계",minimumOutput:"최소 결과물",starterRisk:"초기 위험",stopRule:"중단 조건",
loadingOpportunity:"불러오는 중…",rankLine:"순위 #{rank} · {scope}",detailSubtitle:"기회 {opportunity} · 순위 점수 {rank} · 신뢰도 {confidence}",noExplanation:"기록된 설명이 없습니다.",showingEvidence:"{total}개 중 {shown}개 표시",
detailLoadFailed:"기회 상세 불러오기 실패: {message}",unableLoad:"기회를 불러올 수 없습니다",radarLoadFailed:"Radar 불러오기 실패: {message}",statusError:"오류",scanning:"선택한 언어의 소스를 스캔하는 중…",completed:"완료",scanFailed:"스캔 실패",
scanResult:"{status} · {language}/{country} · 기회 {count}개",handoffReady:"AI Team용 결정적 핸드오프 패키지를 생성할 수 있습니다.",handoffGenerating:"핸드오프 생성 중…",handoffCreated:"생성됨",handoffFailed:"핸드오프 실패",openOpportunityFirst:"먼저 기회를 열어주세요.",
scanFailedMessage:"Radar Lite 스캔 실패: {message}",handoffFailedMessage:"AI Team 핸드오프 실패: {message}",statusHealthy:"정상",statusStored:"저장됨",statusDegraded:"저하",statusStale:"오래됨",statusRateLimited:"요청 제한",statusAuthRequired:"인증 필요",statusFailed:"실패",statusDisabled:"비활성"
};
const fr={
brandEyebrow:"Moteur d’intelligence des signaux",workspaceLabel:"Espace de travail",legacyWorkspace:"Ancien / non classé",scopeLabel:"Périmètre Radar",languageLabel:"Langue",viewResults:"Voir les résultats",runScan:"Lancer le scan Lite",ready:"Prêt",
heroEyebrow:"Opportunités prioritaires du jour",heroTitle:"Découvrez ce qui mérite d’être fait, puis décidez comment le réaliser.",loadingHistory:"Chargement de l’historique Radar…",metricOpportunities:"Opportunités",metricProviders:"Sources",metricLastRun:"Dernier run",
priorityQueue:"File prioritaire",topOpportunities:"Opportunités prioritaires",loading:"Chargement",emptyTitle:"Aucune opportunité à afficher.",emptyHint:"Lancez d’abord Radar Lite, puis revenez consulter les résultats.",
acquisitionHealth:"État de l’acquisition",dataProviders:"Sources de données",detailLabel:"Détail de l’opportunité",close:"Fermer",scoreBreakdown:"Détail du score",whyNow:"Pourquoi maintenant",evidence:"Preuves",sourceConfirmation:"Confirmation des sources",history:"Historique",researchPack:"Pack de recherche",
aiTeamHandoff:"Transmission à AI Team",generateHandoff:"Générer HANDOFF.json",handoffPrompt:"Sélectionnez une opportunité et générez un paquet de transmission déterministe.",noSavedResults:"Aucun résultat Radar enregistré pour « {scope} ».",
candidates:"candidats",deepScans:"scans approfondis",seed:"graine",updated:"mis à jour",statusReady:"Prêt",statusEmpty:"Vide",rankScore:"score de rang",opportunity:"Opportunité",confidence:"Confiance",freshness:"Fraîcheur",evidenceCount:"{count} preuves",
providerNoState:"Aucun état persistant de collecte pour le moment.",events:"Événements",next:"Prochain",last:"Dernier",success:"Succès",componentDemand:"Demande",componentMomentum:"Dynamique",componentSupplyGap:"Écart d’offre",componentAuthorityFit:"Adéquation d’autorité",componentBusinessFit:"Adéquation business",
noSourceSummary:"Aucun résumé de source disponible.",noHistory:"Pas encore d’historique.",historyAria:"Historique du score de rang de l’opportunité",snapshots:"instantanés",noEvidenceDetails:"Aucun détail de preuve pour cet instantané.",unknown:"inconnu",openSource:"Ouvrir la source ↗",
citations:"Citations",questions:"Questions",painRequests:"Problèmes / demandes",evidenceGaps:"Lacunes de preuve",actionCard:"Carte d’action",firstStep:"Première étape",minimumOutput:"Livrable minimum",starterRisk:"Risque initial",stopRule:"Règle d’arrêt",
loadingOpportunity:"Chargement…",rankLine:"Rang #{rank} · {scope}",detailSubtitle:"Opportunité {opportunity} · Score de rang {rank} · Confiance {confidence}",noExplanation:"Aucune explication enregistrée.",showingEvidence:"{shown} sur {total}",
detailLoadFailed:"Échec du chargement du détail : {message}",unableLoad:"Impossible de charger l’opportunité",radarLoadFailed:"Échec du chargement Radar : {message}",statusError:"Erreur",scanning:"Analyse des sources dans la langue sélectionnée…",completed:"Terminé",scanFailed:"Échec du scan",
scanResult:"{status} · {language}/{country} · {count} opportunités",handoffReady:"Prêt à générer un paquet de transmission déterministe pour AI Team.",handoffGenerating:"Génération du handoff…",handoffCreated:"Créé",handoffFailed:"Échec du handoff",openOpportunityFirst:"Ouvrez d’abord une opportunité.",
scanFailedMessage:"Échec du scan Radar Lite : {message}",handoffFailedMessage:"Échec du handoff AI Team : {message}",statusHealthy:"sain",statusStored:"stocké",statusDegraded:"dégradé",statusStale:"périmé",statusRateLimited:"limité",statusAuthRequired:"authentification requise",statusFailed:"échec",statusDisabled:"désactivé"
};
const de={
brandEyebrow:"Signal-Intelligence-Engine",workspaceLabel:"Arbeitsbereich",legacyWorkspace:"Alt / ohne Zuordnung",scopeLabel:"Radar-Bereich",languageLabel:"Sprache",viewResults:"Ergebnisse anzeigen",runScan:"Lite-Scan starten",ready:"Bereit",
heroEyebrow:"Heutige Top-Chancen",heroTitle:"Entdecke zuerst, was sich lohnt, und entscheide dann, wie du es umsetzt.",loadingHistory:"Radar-Verlauf wird geladen…",metricOpportunities:"Chancen",metricProviders:"Quellen",metricLastRun:"Letzter Lauf",
priorityQueue:"Prioritätsliste",topOpportunities:"Top-Chancen",loading:"Laden",emptyTitle:"Noch keine Chancen verfügbar.",emptyHint:"Führe zuerst Radar Lite aus und prüfe danach die Ergebnisse.",
acquisitionHealth:"Erfassungsstatus",dataProviders:"Datenquellen",detailLabel:"Chancen-Details",close:"Schließen",scoreBreakdown:"Score-Aufschlüsselung",whyNow:"Warum jetzt",evidence:"Belege",sourceConfirmation:"Quellenbestätigung",history:"Verlauf",researchPack:"Research Pack",
aiTeamHandoff:"Übergabe an AI Team",generateHandoff:"HANDOFF.json erzeugen",handoffPrompt:"Wähle eine Chance und erzeuge ein deterministisches Übergabepaket.",noSavedResults:"Für „{scope}“ sind keine Radar-Ergebnisse gespeichert.",
candidates:"Kandidaten",deepScans:"Tiefenscans",seed:"Seed",updated:"aktualisiert",statusReady:"Bereit",statusEmpty:"Leer",rankScore:"Rang-Score",opportunity:"Chance",confidence:"Konfidenz",freshness:"Aktualität",evidenceCount:"{count} Belege",
providerNoState:"Noch kein persistenter Collector-Status vorhanden.",events:"Ereignisse",next:"Nächster",last:"Letzter",success:"Erfolg",componentDemand:"Nachfrage",componentMomentum:"Dynamik",componentSupplyGap:"Angebotslücke",componentAuthorityFit:"Autoritäts-Fit",componentBusinessFit:"Business-Fit",
noSourceSummary:"Keine Quellenübersicht verfügbar.",noHistory:"Noch kein Verlauf.",historyAria:"Verlauf des Chancen-Rang-Scores",snapshots:"Snapshots",noEvidenceDetails:"Für diesen Snapshot sind keine Belegdetails verfügbar.",unknown:"unbekannt",openSource:"Quelle öffnen ↗",
citations:"Zitate",questions:"Fragen",painRequests:"Probleme / Anfragen",evidenceGaps:"Beleglücken",actionCard:"Aktionskarte",firstStep:"Erster Schritt",minimumOutput:"Minimales Ergebnis",starterRisk:"Start-Risiko",stopRule:"Abbruchregel",
loadingOpportunity:"Laden…",rankLine:"Rang #{rank} · {scope}",detailSubtitle:"Chance {opportunity} · Rang-Score {rank} · Konfidenz {confidence}",noExplanation:"Keine Erklärung gespeichert.",showingEvidence:"{shown} von {total}",
detailLoadFailed:"Chancen-Details konnten nicht geladen werden: {message}",unableLoad:"Chance kann nicht geladen werden",radarLoadFailed:"Radar konnte nicht geladen werden: {message}",statusError:"Fehler",scanning:"Quellen für die gewählte Sprache werden gescannt…",completed:"Fertig",scanFailed:"Scan fehlgeschlagen",
scanResult:"{status} · {language}/{country} · {count} Chancen",handoffReady:"Bereit für ein deterministisches AI-Team-Übergabepaket.",handoffGenerating:"Übergabe wird erzeugt…",handoffCreated:"Erstellt",handoffFailed:"Übergabe fehlgeschlagen",openOpportunityFirst:"Öffne zuerst eine Chance.",
scanFailedMessage:"Radar-Lite-Scan fehlgeschlagen: {message}",handoffFailedMessage:"AI-Team-Übergabe fehlgeschlagen: {message}",statusHealthy:"gesund",statusStored:"gespeichert",statusDegraded:"degradiert",statusStale:"veraltet",statusRateLimited:"rate-limitiert",statusAuthRequired:"Authentifizierung nötig",statusFailed:"fehlgeschlagen",statusDisabled:"deaktiviert"
};
const es={
brandEyebrow:"Motor de inteligencia de señales",workspaceLabel:"Espacio de trabajo",legacyWorkspace:"Anterior / sin clasificar",scopeLabel:"Ámbito del Radar",languageLabel:"Idioma",viewResults:"Ver resultados",runScan:"Ejecutar escaneo Lite",ready:"Listo",
heroEyebrow:"Oportunidades prioritarias de hoy",heroTitle:"Descubre primero qué merece la pena hacer y luego decide cómo hacerlo.",loadingHistory:"Cargando historial de Radar…",metricOpportunities:"Oportunidades",metricProviders:"Fuentes",metricLastRun:"Última ejecución",
priorityQueue:"Cola prioritaria",topOpportunities:"Oportunidades prioritarias",loading:"Cargando",emptyTitle:"Aún no hay oportunidades para mostrar.",emptyHint:"Ejecuta primero Radar Lite y vuelve para revisar los resultados.",
acquisitionHealth:"Estado de adquisición",dataProviders:"Fuentes de datos",detailLabel:"Detalle de oportunidad",close:"Cerrar",scoreBreakdown:"Desglose de puntuación",whyNow:"Por qué ahora",evidence:"Evidencia",sourceConfirmation:"Confirmación de fuentes",history:"Historial",researchPack:"Paquete de investigación",
aiTeamHandoff:"Entrega a AI Team",generateHandoff:"Generar HANDOFF.json",handoffPrompt:"Selecciona una oportunidad y genera un paquete de entrega determinista.",noSavedResults:"No hay resultados guardados de Radar para “{scope}”.",
candidates:"candidatos",deepScans:"escaneos profundos",seed:"semilla",updated:"actualizado",statusReady:"Listo",statusEmpty:"Vacío",rankScore:"puntuación de ranking",opportunity:"Oportunidad",confidence:"Confianza",freshness:"Actualidad",evidenceCount:"{count} evidencias",
providerNoState:"Aún no hay estado persistente de trabajos de recopilación.",events:"Eventos",next:"Siguiente",last:"Último",success:"Éxito",componentDemand:"Demanda",componentMomentum:"Impulso",componentSupplyGap:"Brecha de oferta",componentAuthorityFit:"Encaje de autoridad",componentBusinessFit:"Encaje de negocio",
noSourceSummary:"No hay resumen de fuentes disponible.",noHistory:"Aún no hay historial.",historyAria:"Historial de puntuación de ranking de la oportunidad",snapshots:"instantáneas",noEvidenceDetails:"No hay detalles de evidencia para esta instantánea.",unknown:"desconocido",openSource:"Abrir fuente ↗",
citations:"Citas",questions:"Preguntas",painRequests:"Problemas / solicitudes",evidenceGaps:"Brechas de evidencia",actionCard:"Tarjeta de acción",firstStep:"Primer paso",minimumOutput:"Resultado mínimo",starterRisk:"Riesgo inicial",stopRule:"Regla de parada",
loadingOpportunity:"Cargando…",rankLine:"Ranking #{rank} · {scope}",detailSubtitle:"Oportunidad {opportunity} · Ranking {rank} · Confianza {confidence}",noExplanation:"No hay explicación registrada.",showingEvidence:"mostrando {shown} de {total}",
detailLoadFailed:"Error al cargar el detalle: {message}",unableLoad:"No se puede cargar la oportunidad",radarLoadFailed:"Error al cargar Radar: {message}",statusError:"Error",scanning:"Escaneando fuentes para el idioma seleccionado…",completed:"Completado",scanFailed:"Escaneo fallido",
scanResult:"{status} · {language}/{country} · {count} oportunidades",handoffReady:"Listo para generar un paquete determinista para AI Team.",handoffGenerating:"Generando handoff…",handoffCreated:"Creado",handoffFailed:"Handoff fallido",openOpportunityFirst:"Abre primero una oportunidad.",
scanFailedMessage:"Falló el escaneo Radar Lite: {message}",handoffFailedMessage:"Falló el handoff de AI Team: {message}",statusHealthy:"saludable",statusStored:"guardado",statusDegraded:"degradado",statusStale:"obsoleto",statusRateLimited:"limitado",statusAuthRequired:"requiere autenticación",statusFailed:"fallido",statusDisabled:"deshabilitado"
};
const pt={
brandEyebrow:"Motor de inteligência de sinais",workspaceLabel:"Workspace",legacyWorkspace:"Legado / sem escopo",scopeLabel:"Escopo do Radar",languageLabel:"Idioma",viewResults:"Ver resultados",runScan:"Executar scan Lite",ready:"Pronto",
heroEyebrow:"Principais oportunidades de hoje",heroTitle:"Descubra primeiro o que vale a pena fazer; depois decida como fazê-lo.",loadingHistory:"Carregando histórico do Radar…",metricOpportunities:"Oportunidades",metricProviders:"Fontes",metricLastRun:"Última execução",
priorityQueue:"Fila prioritária",topOpportunities:"Principais oportunidades",loading:"Carregando",emptyTitle:"Ainda não há oportunidades para exibir.",emptyHint:"Execute o Radar Lite primeiro e depois volte para revisar os resultados.",
acquisitionHealth:"Saúde da aquisição",dataProviders:"Fontes de dados",detailLabel:"Detalhe da oportunidade",close:"Fechar",scoreBreakdown:"Detalhamento da pontuação",whyNow:"Por que agora",evidence:"Evidências",sourceConfirmation:"Confirmação das fontes",history:"Histórico",researchPack:"Pacote de pesquisa",
aiTeamHandoff:"Handoff para AI Team",generateHandoff:"Gerar HANDOFF.json",handoffPrompt:"Selecione uma oportunidade e gere um pacote de handoff determinístico.",noSavedResults:"Não há resultados salvos do Radar para “{scope}”.",
candidates:"candidatos",deepScans:"scans profundos",seed:"semente",updated:"atualizado",statusReady:"Pronto",statusEmpty:"Vazio",rankScore:"pontuação de ranking",opportunity:"Oportunidade",confidence:"Confiança",freshness:"Atualidade",evidenceCount:"{count} evidências",
providerNoState:"Ainda não há estado persistente dos coletores.",events:"Eventos",next:"Próximo",last:"Último",success:"Sucesso",componentDemand:"Demanda",componentMomentum:"Momento",componentSupplyGap:"Lacuna de oferta",componentAuthorityFit:"Aderência de autoridade",componentBusinessFit:"Aderência de negócio",
noSourceSummary:"Nenhum resumo de fonte disponível.",noHistory:"Ainda não há histórico.",historyAria:"Histórico da pontuação de ranking da oportunidade",snapshots:"snapshots",noEvidenceDetails:"Não há detalhes de evidência para este snapshot.",unknown:"desconhecido",openSource:"Abrir fonte ↗",
citations:"Citações",questions:"Perguntas",painRequests:"Problemas / pedidos",evidenceGaps:"Lacunas de evidência",actionCard:"Cartão de ação",firstStep:"Primeiro passo",minimumOutput:"Entrega mínima",starterRisk:"Risco inicial",stopRule:"Regra de parada",
loadingOpportunity:"Carregando…",rankLine:"Ranking #{rank} · {scope}",detailSubtitle:"Oportunidade {opportunity} · Ranking {rank} · Confiança {confidence}",noExplanation:"Nenhuma explicação registrada.",showingEvidence:"mostrando {shown} de {total}",
detailLoadFailed:"Falha ao carregar detalhe: {message}",unableLoad:"Não foi possível carregar a oportunidade",radarLoadFailed:"Falha ao carregar Radar: {message}",statusError:"Erro",scanning:"Escaneando fontes no idioma selecionado…",completed:"Concluído",scanFailed:"Falha no scan",
scanResult:"{status} · {language}/{country} · {count} oportunidades",handoffReady:"Pronto para gerar um pacote determinístico para AI Team.",handoffGenerating:"Gerando handoff…",handoffCreated:"Criado",handoffFailed:"Falha no handoff",openOpportunityFirst:"Abra uma oportunidade primeiro.",
scanFailedMessage:"Falha no scan do Radar Lite: {message}",handoffFailedMessage:"Falha no handoff do AI Team: {message}",statusHealthy:"saudável",statusStored:"armazenado",statusDegraded:"degradado",statusStale:"desatualizado",statusRateLimited:"limitado",statusAuthRequired:"autenticação necessária",statusFailed:"falhou",statusDisabled:"desativado"
};

Object.assign(fr,{
loadingHistory:"Chargement de l’historique Radar…",emptyHint:"Lancez d’abord Radar Lite, puis revenez consulter les résultats.",
noSavedResults:"Aucun résultat Radar enregistré pour « {scope} ».",candidates:"candidats",deepScans:"scans approfondis",seed:"graine",updated:"mis à jour",
statusReady:"Prêt",statusEmpty:"Vide",rankScore:"score de rang",evidenceCount:"{count} preuves",providerNoState:"Aucun état persistant de collecte pour le moment.",
events:"Événements",next:"Prochain",last:"Dernier",success:"Succès",componentDemand:"Demande",componentMomentum:"Dynamique",componentSupplyGap:"Écart d’offre",
componentAuthorityFit:"Adéquation d’autorité",componentBusinessFit:"Adéquation business",noSourceSummary:"Aucun résumé de source disponible.",noHistory:"Pas encore d’historique.",
historyAria:"Historique du score de rang de l’opportunité",snapshots:"instantanés",noEvidenceDetails:"Aucun détail de preuve pour cet instantané.",unknown:"inconnu",
loadingOpportunity:"Chargement…",rankLine:"Rang #{rank} · {scope}",detailSubtitle:"Opportunité {opportunity} · Score de rang {rank} · Confiance {confidence}",
noExplanation:"Aucune explication enregistrée.",showingEvidence:"{shown} sur {total}",detailLoadFailed:"Échec du chargement du détail : {message}",
unableLoad:"Impossible de charger l’opportunité",radarLoadFailed:"Échec du chargement Radar : {message}",scanning:"Analyse des sources dans la langue sélectionnée…",
scanResult:"{status} · {language}/{country} · {count} opportunités",handoffReady:"Prêt à générer un paquet de transmission déterministe pour AI Team.",
handoffGenerating:"Génération du handoff…",openOpportunityFirst:"Ouvrez d’abord une opportunité.",scanFailedMessage:"Échec du scan Radar Lite : {message}",
handoffFailedMessage:"Échec du handoff AI Team : {message}",statusStale:"périmé",statusRateLimited:"limité",statusAuthRequired:"authentification requise"
});
Object.assign(de,{
loadingHistory:"Radar-Verlauf wird geladen…",emptyHint:"Führe zuerst Radar Lite aus und prüfe danach die Ergebnisse.",
noSavedResults:"Für „{scope}“ sind keine Radar-Ergebnisse gespeichert.",candidates:"Kandidaten",deepScans:"Tiefenscans",seed:"Seed",updated:"aktualisiert",
statusReady:"Bereit",statusEmpty:"Leer",rankScore:"Rang-Score",evidenceCount:"{count} Belege",providerNoState:"Noch kein persistenter Collector-Status vorhanden.",
events:"Ereignisse",next:"Nächster",last:"Letzter",success:"Erfolg",componentDemand:"Nachfrage",componentMomentum:"Dynamik",componentSupplyGap:"Angebotslücke",
componentAuthorityFit:"Autoritäts-Fit",componentBusinessFit:"Business-Fit",noSourceSummary:"Keine Quellenübersicht verfügbar.",noHistory:"Noch kein Verlauf.",
historyAria:"Verlauf des Chancen-Rang-Scores",snapshots:"Snapshots",noEvidenceDetails:"Für diesen Snapshot sind keine Belegdetails verfügbar.",unknown:"unbekannt",
loadingOpportunity:"Laden…",rankLine:"Rang #{rank} · {scope}",detailSubtitle:"Chance {opportunity} · Rang-Score {rank} · Konfidenz {confidence}",
noExplanation:"Keine Erklärung gespeichert.",showingEvidence:"{shown} von {total}",detailLoadFailed:"Chancen-Details konnten nicht geladen werden: {message}",
unableLoad:"Chance kann nicht geladen werden",radarLoadFailed:"Radar konnte nicht geladen werden: {message}",scanning:"Quellen für die gewählte Sprache werden gescannt…",
scanResult:"{status} · {language}/{country} · {count} Chancen",handoffReady:"Bereit für ein deterministisches AI-Team-Übergabepaket.",
handoffGenerating:"Übergabe wird erzeugt…",openOpportunityFirst:"Öffne zuerst eine Chance.",scanFailedMessage:"Radar-Lite-Scan fehlgeschlagen: {message}",
handoffFailedMessage:"AI-Team-Übergabe fehlgeschlagen: {message}",statusStale:"veraltet",statusRateLimited:"rate-limitiert",statusAuthRequired:"Authentifizierung nötig"
});
Object.assign(es,{
loadingHistory:"Cargando historial de Radar…",emptyHint:"Ejecuta primero Radar Lite y vuelve para revisar los resultados.",
noSavedResults:"No hay resultados guardados de Radar para “{scope}”.",candidates:"candidatos",deepScans:"escaneos profundos",seed:"semilla",updated:"actualizado",
statusReady:"Listo",statusEmpty:"Vacío",rankScore:"puntuación de ranking",evidenceCount:"{count} evidencias",providerNoState:"Aún no hay estado persistente de trabajos de recopilación.",
events:"Eventos",next:"Siguiente",last:"Último",success:"Éxito",componentDemand:"Demanda",componentMomentum:"Impulso",componentSupplyGap:"Brecha de oferta",
componentAuthorityFit:"Encaje de autoridad",componentBusinessFit:"Encaje de negocio",noSourceSummary:"No hay resumen de fuentes disponible.",noHistory:"Aún no hay historial.",
historyAria:"Historial de puntuación de ranking de la oportunidad",snapshots:"instantáneas",noEvidenceDetails:"No hay detalles de evidencia para esta instantánea.",unknown:"desconocido",
loadingOpportunity:"Cargando…",rankLine:"Ranking #{rank} · {scope}",detailSubtitle:"Oportunidad {opportunity} · Ranking {rank} · Confianza {confidence}",
noExplanation:"No hay explicación registrada.",showingEvidence:"mostrando {shown} de {total}",detailLoadFailed:"Error al cargar el detalle: {message}",
unableLoad:"No se puede cargar la oportunidad",radarLoadFailed:"Error al cargar Radar: {message}",scanning:"Escaneando fuentes para el idioma seleccionado…",
scanResult:"{status} · {language}/{country} · {count} oportunidades",handoffReady:"Listo para generar un paquete determinista para AI Team.",
handoffGenerating:"Generando handoff…",openOpportunityFirst:"Abre primero una oportunidad.",scanFailedMessage:"Falló el escaneo Radar Lite: {message}",
handoffFailedMessage:"Falló el handoff de AI Team: {message}",statusStale:"obsoleto",statusRateLimited:"limitado",statusAuthRequired:"requiere autenticación"
});
Object.assign(pt,{
loadingHistory:"Carregando histórico do Radar…",emptyHint:"Execute o Radar Lite primeiro e depois volte para revisar os resultados.",
noSavedResults:"Não há resultados salvos do Radar para “{scope}”.",candidates:"candidatos",deepScans:"scans profundos",seed:"semente",updated:"atualizado",
statusReady:"Pronto",statusEmpty:"Vazio",rankScore:"pontuação de ranking",evidenceCount:"{count} evidências",providerNoState:"Ainda não há estado persistente dos coletores.",
events:"Eventos",next:"Próximo",last:"Último",success:"Sucesso",componentDemand:"Demanda",componentMomentum:"Momento",componentSupplyGap:"Lacuna de oferta",
componentAuthorityFit:"Aderência de autoridade",componentBusinessFit:"Aderência de negócio",noSourceSummary:"Nenhum resumo de fonte disponível.",noHistory:"Ainda não há histórico.",
historyAria:"Histórico da pontuação de ranking da oportunidade",snapshots:"snapshots",noEvidenceDetails:"Não há detalhes de evidência para este snapshot.",unknown:"desconhecido",
loadingOpportunity:"Carregando…",rankLine:"Ranking #{rank} · {scope}",detailSubtitle:"Oportunidade {opportunity} · Ranking {rank} · Confiança {confidence}",
noExplanation:"Nenhuma explicação registrada.",showingEvidence:"mostrando {shown} de {total}",detailLoadFailed:"Falha ao carregar detalhe: {message}",
unableLoad:"Não foi possível carregar a oportunidade",radarLoadFailed:"Falha ao carregar Radar: {message}",scanning:"Escaneando fontes no idioma selecionado…",
scanResult:"{status} · {language}/{country} · {count} oportunidades",handoffReady:"Pronto para gerar um pacote determinístico para AI Team.",
handoffGenerating:"Gerando handoff…",openOpportunityFirst:"Abra uma oportunidade primeiro.",scanFailedMessage:"Falha no scan do Radar Lite: {message}",
handoffFailedMessage:"Falha no handoff do AI Team: {message}",statusStale:"desatualizado",statusRateLimited:"limitado",statusAuthRequired:"autenticação necessária"
});
// 中文说明：工作区管理的 8 语文案统一补齐，避免不同语言出现中文硬编码。
const workspaceLabels={
  en:["Unassigned results (current scope)","New","Edit","New workspace","Edit workspace","Name","Focus topic","Language","Cancel","Save workspace"],
  zh:["未归入工作区（按当前范围）","新建","编辑","新建工作区","编辑工作区","名称","关注主题","语言","取消","保存工作区"],
  ja:["すべての履歴（未分類）","新規","編集","ワークスペースを作成","ワークスペースを編集","名前","対象テーマ","言語","キャンセル","保存"],
  ko:["전체 기록(미분류)","새로 만들기","편집","작업 공간 만들기","작업 공간 편집","이름","관심 주제","언어","취소","저장"],
  fr:["Tout l'historique (non classé)","Créer","Modifier","Nouvel espace","Modifier l’espace","Nom","Sujet suivi","Langue","Annuler","Enregistrer"],
  de:["Gesamter Verlauf (nicht zugeordnet)","Neu","Bearbeiten","Neuer Arbeitsbereich","Arbeitsbereich bearbeiten","Name","Thema","Sprache","Abbrechen","Speichern"],
  es:["Todo el historial (sin clasificar)","Nuevo","Editar","Nuevo espacio","Editar espacio","Nombre","Tema","Idioma","Cancelar","Guardar"],
  pt:["Todo o histórico (sem categoria)","Novo","Editar","Novo espaço","Editar espaço","Nome","Tema","Idioma","Cancelar","Salvar"],
};
const workspaceKeys=["legacyWorkspace","newWorkspaceButton","editWorkspaceButton","newWorkspace","editWorkspace","workspaceName","workspaceScope","workspaceLanguage","cancel","saveWorkspace"];
Object.entries(workspaceLabels).forEach(([language,values])=>{
  const pack={en,zh,ja,ko,fr,de,es,pt}[language];
  workspaceKeys.forEach((key,index)=>{pack[key]=values[index];});
});
const packs={en,zh,ja,ko,fr,de,es,pt};
const locales={zh:"zh-CN",en:"en-US",ja:"ja-JP",ko:"ko-KR",fr:"fr-FR",de:"de-DE",es:"es-ES",pt:"pt-BR"};
function normalize(language){const base=String(language||"en").toLowerCase().split("-",1)[0];return packs[base]?base:"en";}
function get(language){return packs[normalize(language)];}
function t(language,key){return get(language)[key]??en[key]??key;}
function tf(language,key,values={}){return String(t(language,key)).replace(/\{([a-zA-Z0-9_]+)\}/g,(_,name)=>Object.prototype.hasOwnProperty.call(values,name)?String(values[name]):"{"+name+"}");}
function locale(language){return locales[normalize(language)]||locales.en;}
function applyStatic(root,language){
  root.querySelectorAll("[data-i18n]").forEach(node=>{node.textContent=t(language,node.dataset.i18n);});
  root.querySelectorAll("[data-i18n-aria]").forEach(node=>{node.setAttribute("aria-label",t(language,node.dataset.i18nAria));});
}
window.RadarI18n={packs,normalize,get,t,tf,locale,applyStatic};
})();
