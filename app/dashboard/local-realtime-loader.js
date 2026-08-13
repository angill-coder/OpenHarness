(function () {
  'use strict';

  const DEFAULTS = {
    user: 'unattributed', userLabel: '\u672a\u8bb0\u5f55', sessionsRoot: 'app/sessions',
    refreshMs: 2 * 1000, requestTimeoutMs: 10 * 1000, requestRetries: 1,
  };
  const config = Object.assign({}, DEFAULTS, window.OPENHARNESS_LOCAL_CONFIG || {});
  const apiBase = '/api/local';
  let loadedRevision = null;
  let activeBundles = [];
  let activeTree = [];
  let activeSkippedSessionCount = 0;
  let refreshPromise = null;
  const rawPackagePromises = new Map();
  const metadataDocumentPromises = new Map();
  const qualityDocumentPromises = new Map();
  const structuredDocumentPromises = new Map();
  const skillSourcePromises = new Map();
  const outputPromises = new Map();
  const tracePromises = new Map();
  const judgmentDetailPromises = new Map();

  function unique(values) {
    return [...new Set(values)];
  }
  function displayTerminology(value) {
    return String(value ?? '')
      .replace(/(^|[^A-Za-z0-9_])metadata(?=$|[^A-Za-z0-9_])/ig, '$1Structured Data')
      .replace(/ground[ _-]*truth/ig, 'Human Report');
  }
  function parseJsonLines(text) {
    const lines = String(text || '').split(/\r?\n/).filter(line => line.trim());
    return lines.flatMap((line, index) => {
      try {
        return [JSON.parse(line)];
      } catch (error) {
        if (index === lines.length - 1) return [];
        throw error;
      }
    });
  }

  async function fetchResponse(url, options) {
    let lastError = null;
    for (let attempt = 0; attempt <= config.requestRetries; attempt += 1) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), config.requestTimeoutMs);
      try {
        return await fetch(url, Object.assign({}, options, { signal: controller.signal }));
      } catch (error) {
        lastError = error;
        if (attempt < config.requestRetries) {
          await new Promise(resolve => window.setTimeout(resolve, 300 * (attempt + 1)));
        }
      } finally {
        window.clearTimeout(timeout);
      }
    }
    throw lastError || new Error(`请求失败: ${url}`);
  }

  async function fetchJson(url) {
    const response = await fetchResponse(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
    return response.json();
  }

  async function fetchText(url) {
    const response = await fetchResponse(url, {});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
    return response.text();
  }

  function sessionFiles(tree) {
    const sessions = new Map();
    const prefix = config.sessionsRoot + '/';
    tree.forEach(item => {
      if (item.type !== 'blob' || !item.path.startsWith(prefix)) return;
      const rest = item.path.slice(prefix.length);
      const slash = rest.indexOf('/');
      if (slash < 1) return;
      const sessionId = rest.slice(0, slash);
      const file = rest.slice(slash + 1);
      if (!sessions.has(sessionId)) {
        sessions.set(sessionId, { files: new Set(), revisions: [] });
      }
      const entry = sessions.get(sessionId);
      entry.files.add(file);
      entry.revisions.push(file + ':' + (item.revision || item.size || 0));
    });
    sessions.forEach(entry => {
      entry.revision = entry.revisions.sort().join('|');
      delete entry.revisions;
    });
    return sessions;
  }
  function isReadable(entry) { return entry.files.has('state.json'); }

  function rawUrl(path, revision) {
    return apiBase + '/file?path=' + encodeURIComponent(path) + '&rev=' + encodeURIComponent(revision || '');
  }

  function rawPackageUrl(sessionId, caseId) {
    return apiBase + '/raw-package?session=' + encodeURIComponent(sessionId) + '&case_id=' + encodeURIComponent(caseId);
  }

  function skillSourceUrl(sessionId, version) {
    const query = new URLSearchParams({ session: sessionId, version });
    return apiBase + '/skill-source?' + query.toString();
  }

  function generationTraceUrl(sessionId, version, caseId, generationId) {
    const query = new URLSearchParams({
      session: sessionId,
      version,
      case_id: caseId,
      generation_id: generationId,
    });
    return apiBase + '/generation-trace?' + query.toString();
  }
  function judgmentDetailUrl(sessionId, version, caseId) {
    const query = new URLSearchParams({
      session: sessionId,
      version,
      case_id: caseId,
    });
    return apiBase + '/case-judgment?' + query.toString();
  }  function structuredDocumentUrl(sessionId, caseId) {
    return apiBase + '/structured-case?session=' + encodeURIComponent(sessionId) + '&case_id=' + encodeURIComponent(caseId);
  }  function metadataDocumentUrl(sessionId, caseId) {
    return apiBase + '/case-metadata?session=' + encodeURIComponent(sessionId) + '&case_id=' + encodeURIComponent(caseId);
  }

  function qualityDocumentUrl(sessionId, caseId) {
    return apiBase + '/case-quality?session=' + encodeURIComponent(sessionId) + '&case_id=' + encodeURIComponent(caseId);
  }

  function retryable(cache, key, promise) {
    return promise.catch(error => {
      cache.delete(key);
      throw error;
    });
  }

  function loadRawPackage(sessionId, caseId, revision) {
    const key = sessionId + '|' + caseId + '|' + (revision || '');
    if (!rawPackagePromises.has(key)) {
      const request = fetchJson(rawPackageUrl(sessionId, caseId))
        .then(payload => payload.files || []);
      rawPackagePromises.set(key, retryable(rawPackagePromises, key, request));
    }
    return rawPackagePromises.get(key);
  }

  function loadMetadataDocument(sessionId, caseId, revision) {
    const key = sessionId + '|' + caseId + '|' + (revision || '');
    if (!metadataDocumentPromises.has(key)) {
      const request = fetchJson(metadataDocumentUrl(sessionId, caseId));
      metadataDocumentPromises.set(key, retryable(metadataDocumentPromises, key, request));
    }
    return metadataDocumentPromises.get(key);
  }

  function loadQualityDocument(sessionId, caseId, revision) {
    const key = sessionId + '|' + caseId + '|' + (revision || '');
    if (!qualityDocumentPromises.has(key)) {
      const request = fetchJson(qualityDocumentUrl(sessionId, caseId))
        .catch(error => ({ available: false, error: error.message, scores: {}, details: [] }));
      qualityDocumentPromises.set(key, request);
    }
    return qualityDocumentPromises.get(key);
  }

  function loadStructuredDocument(sessionId, caseId, revision) {
    const key = sessionId + '|' + caseId + '|' + (revision || '');
    if (!structuredDocumentPromises.has(key)) {
      const request = fetchJson(structuredDocumentUrl(sessionId, caseId));
      structuredDocumentPromises.set(
        key, retryable(structuredDocumentPromises, key, request));
    }
    return structuredDocumentPromises.get(key);
  }
  function loadOutputDocument(sessionId, version, caseId, revision) {
    const key = sessionId + '|' + version + '|' + caseId + '|' + (revision || '');
    if (!outputPromises.has(key)) {
      const query = new URLSearchParams({ session: sessionId, version, case_id: caseId });
      const request = fetchJson(apiBase + '/case-output?' + query.toString());
      outputPromises.set(key, retryable(outputPromises, key, request));
    }
    return outputPromises.get(key);
  }

  async function loadBundle(sessionId, files, revision) {
    const summary = await fetchJson(
      apiBase + '/session-summary?session=' + encodeURIComponent(sessionId)
        + '&rev=' + encodeURIComponent(revision || '')
    );
    return {
      sessionId,
      meta: summary.meta || {},
      state: summary.state || {},
      runtimeSources: summary.runtime_sources || {},
      runtimeModels: summary.runtime_models || {},
      generationSkillSources: {},
      rawPackages: {},
      metadataDocuments: {},
      structuredDocuments: {},
      qualityDocuments: {},
      outputs: [],
      judgments: summary.judgments || [],
      judgmentFile: summary.judgment_file || null,
      revision,
    };
  }
  const RESEARCH_DIMENSION_IDS=[
    'traceability','structure','narrative','insight','coverage','expression'
  ];

  function dimensionsFor(state) {
    return (state.rubric?.dimensions || []).map(dimension => ({
      id: dimension.name || dimension.id,
      label: dimension.name_zh || dimension.label || dimension.name || dimension.id,
      weight: Number(dimension.weight || 0),
      checks: (dimension.checks || []).map(check => ({
        id: check.id,
        label: check.label || check.id,
        desc: check.desc || check.description || '',
        redline: Boolean(check.redline),
      })),
    }));
  }
  function hasResearchDimensions(state) {
    const ids=new Set(dimensionsFor(state).map(item=>item.id));
    return RESEARCH_DIMENSION_IDS.every(id=>ids.has(id));
  }


  function bundleTimestamp(bundle) {
    const saved = bundle.state._saved_at;
    if (typeof saved === 'number') return saved;
    const parsed = Date.parse(saved || '') / 1000;
    return Number.isFinite(parsed) ? parsed : Number(bundle.meta.created_at || 0);
  }


  function versionItems(state) {
    return (state.versions || []).map(entry => entry.skill || entry).filter(skill => skill?.version);
  }

  function instructionMarkdown(skill) {
    const instructions = skill.instructions || {};
    const directives = instructions.directives || {};
    const lines = [`# instruction.md · ${skill.version}`, '', '## Prose', '', instructions.prose || '未记录独立 prose。'];
    const names = Object.keys(directives);
    if (names.length) {
      lines.push('', '## Directives', '');
      names.forEach(name => lines.push(`- [${directives[name] ? 'x' : ' '}] \`${name}\``));
    }
    return lines.join('\n');
  }

  function findRepoFile(treePaths, name) {
    const normalized = String(name || '').replace(/\\/g, '/');
    const parts = normalized.split('/').filter(Boolean);
    const tail = parts[parts.length - 1];
    if (!tail) return null;
    return treePaths.find(path => path === normalized) || treePaths.find(path => path.endsWith(`/${normalized}`)) ||
      treePaths.find(path => path.endsWith(`/${tail}`)) || null;
  }

  function caseDataFor(item, treePaths, revision, rawFiles, metadataDocument, structuredDocument, qualityDocument) {
    const input = item.input || {};
    const shallowMetadata = Object.prototype.hasOwnProperty.call(item, 'metadata') ? item.metadata : {};
    const rawMetadata = metadataDocument && Object.prototype.hasOwnProperty.call(metadataDocument, 'metadata')
      ? metadataDocument.metadata : shallowMetadata;
    const metadata = shallowMetadata && typeof shallowMetadata === 'object' ? shallowMetadata : {};
    const sourceRows = Array.isArray(input.sources) ? input.sources : [];
    const files = (rawFiles || []).map(file => [
      file.path || file.name,
      file.type || String(file.extension || 'FILE').toUpperCase(),
      file.url,
      Number(file.size || 0),
    ]);
    sourceRows.forEach(source => {
      const fileName = source.meta?.file || source.file || source.title;
      String(fileName || '').split(/\s*\/\s*/).forEach(candidate => {
        const path = findRepoFile(treePaths, candidate);
        if (!path) return;
        const suffix = path.split('.').pop().toUpperCase();
        files.push([path.split('/').pop(), suffix, rawUrl(path, revision)]);
      });
    });
    return {
      sample: metadata.source_file || metadata.display_name || item.topic || item.case_id,
      range: metadata.range || '以实验记录为准',
      scope: item.audience || input.brief || '未记录目标受众',
      questions: item.key_questions || (item.turns || []).filter(turn => turn.role === 'user').map(turn => turn.content),
      files: unique(files.map(file => JSON.stringify(file))).map(file => JSON.parse(file)),
      metadata,
      rawMetadata,
      metadataSource: metadataDocument?.source || 'state.json · case.metadata',
      metadataDocumentType: metadataDocument?.document_type || 'state_case_metadata',
      metadataEvidenceCount: metadataDocument?.evidence_count ?? null,
      quality: qualityDocument || { available: false, scores: {}, details: [] },
      structuredData: structuredDocument?.case || null,
      structuredSource: structuredDocument?.source || null,
      rawCase: item,
      requiredSections: item.required_sections || [],
    };
  }

  function snapshotFrom(bundles, tree, revision, skippedCount) {
    const allOrderedBundles = bundles.slice().sort((a, b) => bundleTimestamp(b) - bundleTimestamp(a));
    const researchBundles = allOrderedBundles.filter(bundle=>hasResearchDimensions(bundle.state));
    const orderedBundles = researchBundles.length ? researchBundles : allOrderedBundles;
    if(researchBundles.length){
      skippedCount += allOrderedBundles.length - researchBundles.length;
    }
    const canonical = orderedBundles[0];
    if (!canonical) throw new Error('没有找到具有完整 state、outputs 和 judgments 的实验会话。');
    const dimensionDefinitions = orderedBundles.flatMap(bundle => dimensionsFor(bundle.state));
    const dimensions = [...new Map(dimensionDefinitions.map(item => [item.id, item])).values()];
    if (!dimensions.length) throw new Error('实验未提供 rubric.dimensions。');
    const treePaths = tree.filter(item => item.type === 'blob').map(item => item.path);
    const caseItems = [...new Map(orderedBundles.flatMap(bundle => bundle.state.cases || [])
      .map(item => [item.case_id, item])).values()];
    const cases = caseItems.map(item => [item.case_id, item.topic || item.metadata?.display_name || item.case_id]);
    const caseData = caseItems.map(item => {
      const owner = orderedBundles.find(bundle => bundle.rawPackages?.[item.case_id]?.length || bundle.metadataDocuments?.[item.case_id] || bundle.structuredDocuments?.[item.case_id] || bundle.qualityDocuments?.[item.case_id]);
      return caseDataFor(item, treePaths, revision, owner?.rawPackages?.[item.case_id] || [], owner?.metadataDocuments?.[item.case_id], owner?.structuredDocuments?.[item.case_id], owner?.qualityDocuments?.[item.case_id]);
    });
    const rubrics = [...new Map(dimensionDefinitions.flatMap(dimension => dimension.checks.map(check => [
      check.id, dimension.label, check.label, check.desc, check.redline ? '红线' : '评分',
    ])).map(item => [item[0], item])).values()];
    const records = {};
    const versionMetrics = {};
    const skills = {};
    const experiments = [];
    const experimentCaseIds = {};
    const experimentVersionCaseIds = {};
    const experimentRubricIds = {};
    const caseDataByExperiment = {};
    let judgmentCount = 0;

    orderedBundles.forEach(bundle => {
      const localDimensions = dimensionsFor(bundle.state);
      if (!localDimensions.length) return;
      const localCases = bundle.state.cases || [];
      const localCaseIds = localCases.map(item => item.case_id);
      experimentCaseIds[bundle.sessionId] = localCaseIds;
      Object.entries(bundle.state.generation_version_cases || {}).forEach(([version, ids]) => {
        experimentVersionCaseIds[`${bundle.sessionId}|${version}`] = Array.isArray(ids) ? ids : [];
      });
      experimentRubricIds[bundle.sessionId] = localDimensions.flatMap(item => item.checks.map(check => check.id));
      localCases.forEach(item => {
        caseDataByExperiment[`${bundle.sessionId}|${item.case_id}`] = caseDataFor(item, treePaths, revision, bundle.rawPackages?.[item.case_id] || [], bundle.metadataDocuments?.[item.case_id], bundle.structuredDocuments?.[item.case_id], bundle.qualityDocuments?.[item.case_id]);
      });
      const localDimensionIndex = new Map(localDimensions.map((item, index) => [item.id, index]));
      const outputMap = new Map(bundle.outputs.map(row => [`${row.version}|${row.case_id}`, row]));
      const judgmentMap = new Map();
      bundle.judgments.forEach(row => {
        const key = `${row.version}|${row.case_id}`;
        if (row.invalidated) judgmentMap.delete(key);
        else judgmentMap.set(key, row);
      });
      const versions = versionItems(bundle.state);
      if (!versions.length) return;
      const inputModes = unique((bundle.state.cases || []).map(item => item.metadata?.experiment_input_mode).filter(Boolean));
      const explicitData = bundle.state.experiment_data || bundle.meta.experiment_data || {};
      const explicitOptimizer = bundle.state.experiment_optimizer || bundle.meta.experiment_optimizer || {};
      const explicitUser = String(
        bundle.state.experiment_user || bundle.meta.experiment_user || ''
      ).trim();
      const explicitOwner = bundle.state.experiment_owner || bundle.meta.experiment_owner || {};
      const explicitJudge = bundle.state.experiment_judge || bundle.meta.experiment_judge || {};
      const optimizerModel = bundle.runtimeModels.optimizer?.model || null;
      const judgeModel = bundle.runtimeModels.judge?.model || null;
      const versionModels = Object.fromEntries(versions.map(skill => {
        const runtime = bundle.runtimeModels.versions?.[skill.version] || {};
        return [skill.version, {
          optimizerModel: runtime.optimizer?.status === 'not_called'
            ? '\u672a\u8c03\u7528 Optimizer'
            : displayTerminology(runtime.optimizer?.model || runtime.optimizer?.llm_backend || ''),
          judgeModel: displayTerminology(runtime.judge?.model || runtime.judge?.llm_backend || ''),
          judgeCaseId: runtime.judge?.case_id || '',
        }];
      }));

      const dataId = (typeof explicitData === 'string' ? explicitData : explicitData.id) || (inputModes.length === 1 ? inputModes[0] : (bundle.state.product_id || bundle.meta.product_id || 'experiment-data'));
      const optimizer = explicitOptimizer.id || bundle.state.optimizer_mode || 'openharness';
      const judge = String(explicitJudge.id || bundle.state.judge_version || bundle.meta.judge_version || 'v1').toLowerCase();
      const judgeBasis = explicitJudge.basis || (judge === 'v3' ? 'source' : 'groundtruth');
      const owner = explicitUser
        ? [explicitUser.toLowerCase(), explicitUser]
        : explicitOwner.id
        ? [explicitOwner.id, explicitOwner.label || explicitOwner.id]
        : [config.user, config.userLabel];
      const experiment = {
        id: bundle.sessionId,
        session: bundle.sessionId,
        sessionLabel: displayTerminology(bundle.state.session_label || bundle.meta.session_label || bundle.sessionId),
        data: dataId,
        dataLabel: displayTerminology((typeof explicitData === 'string' ? explicitData : explicitData.label) || (inputModes.length === 1 ? inputModes[0] : (bundle.meta.product_id || bundle.state.product_id || 'OpenHarness Data'))),
        optimizer,
        optimizerLabel: displayTerminology(explicitOptimizer.label || (optimizer === 'openharness' ? 'OpenHarness' : optimizer)),
        optimizerModel: displayTerminology(optimizerModel || ''),
        user: owner[0],
        judge,
        judgeLabel: displayTerminology(explicitJudge.label || ('Judge ' + judge.toUpperCase())),
        judgeModel: displayTerminology(judgeModel || ''),
        versionModels,
        judgeBasis,
        userLabel: displayTerminology(owner[1]),
        versions: versions.map(skill => skill.version),
        parents: Object.fromEntries(versions.map(skill => [skill.version, skill.parent_version || null])),
        latestVersion: versions[versions.length - 1].version,
        savedAt: bundle.state._saved_at || bundle.meta.created_at || null,
      };
      experiments.push(experiment);
      versions.forEach(skill => {
        const artifact = bundle.generationSkillSources?.[skill.version] || null;
        skills[`${bundle.sessionId}|${skill.version}`] = {
          parent: skill.parent_version || null,
          skillMd: artifact?.skill_md || '',
          instructionMd: artifact?.instruction_md || '',
          source: artifact?.source || '',
          missing: !artifact,
          changelog: skill.changelog || '',
        };
      });
      judgmentMap.forEach((judgment, key) => {
        if (judgment.scoring_status !== 'scored') return;
        const output = outputMap.get(key) || {};
        const localScores = judgment.scores || {};
        const localScored = {
          dims: localDimensions.map(dimension => Number(localScores[dimension.id] ?? 0)),
          total: Number(judgment.overall ?? 0),
          red: Array.isArray(judgment.redline_checks) ? judgment.redline_checks.length : 0,
          caseFailedGate: Boolean(judgment.case_failed_gate),
        };
        const scored = Object.assign({}, localScored, {
          dims: dimensions.map(dimension => localScored.dims[localDimensionIndex.get(dimension.id)] ?? 0),
        });
        records[`${bundle.sessionId}|${key}`] = Object.assign({}, scored, {
          checks: judgment.checks || {},
          reasoning: judgment.reasoning || {},
          scoreSource: judgment.score_source || '',
          scoringStatus: judgment.scoring_status,
          report: output.report_text || '',
          generationId: output.generation_id || '',
          reportSha256: judgment.report_sha256 || null,
          rubricSha256: judgment.rubric_sha256 || null,
          trace: output.generationRunTrace || { status: 'missing', operations: [], rounds: [], conversation: [], conversationText: '', conversationAvailable: false, source: '' },
        });
        judgmentCount += 1;
      });
      experiment.versions.forEach(version => {
        const rows = localCaseIds.map(caseId => records[`${bundle.sessionId}|${version}|${caseId}`]).filter(Boolean);
        if (!rows.length) return;
        versionMetrics[`${bundle.sessionId}|${version}`] = {
          dims: dimensions.map((dimension, index) => localDimensionIndex.has(dimension.id) ? Number((rows.reduce((sum, row) => sum + row.dims[index], 0) / rows.length).toFixed(4)) : 0),
          total: Number((rows.reduce((sum, row) => sum + row.total, 0) / rows.length).toFixed(4)),
          red: rows.reduce((sum, row) => sum + row.red, 0),
          caseCount: rows.length,
        };
      });
    });

    const naturalExperimentOrder = new Intl.Collator('zh-CN', {
      numeric: true,
      sensitivity: 'base',
    });
    experiments.sort((left, right) => {
      const leftMetric = versionMetrics[`${left.session}|${left.latestVersion}`];
      const rightMetric = versionMetrics[`${right.session}|${right.latestVersion}`];
      const leftHasResult = Boolean(leftMetric?.caseCount && leftMetric.total > 0);
      const rightHasResult = Boolean(rightMetric?.caseCount && rightMetric.total > 0);
      const resultDifference = Number(rightHasResult) - Number(leftHasResult);
      if (resultDifference) return resultDifference;
      return naturalExperimentOrder.compare(
        left.sessionLabel || left.session,
        right.sessionLabel || right.session
      );
    });

    return {
      meta: {
        name: 'OpenHarness \u00b7 Local Realtime',
        source: 'local',
        branch: 'local',
        commit: revision,
        syncedAt: new Date().toISOString(),
        experimentCount: experiments.length,
        caseCount: cases.length,
        judgmentCount,
        checkCount: rubrics.length,
        skippedSessionCount: skippedCount,
      },
      sessions: experiments.map(item => [item.session, item.sessionLabel]),
      dataTypes: unique(experiments.map(item => JSON.stringify([item.data, item.dataLabel]))).map(item => JSON.parse(item)),
      optimizers: unique(experiments.map(item => JSON.stringify([item.optimizer, item.optimizerLabel]))).map(item => JSON.parse(item)),
      users: unique(experiments.map(item => JSON.stringify([item.user, item.userLabel]))).map(item => JSON.parse(item)),
      judges: unique(experiments.map(item => JSON.stringify([item.judge, item.judgeLabel]))).map(item => JSON.parse(item)),
      dimensions: dimensions.map(({ id, label, weight }) => ({ id, label, weight })),
      cases,
      caseData,
      rubrics,
      experimentCaseIds,
      experimentVersionCaseIds,
      experimentRubricIds,
      caseDataByExperiment,
      experiments,
      records,
      versionMetrics,
      skills,
      runtimeSources: Object.fromEntries(orderedBundles.map(bundle => [bundle.sessionId, bundle.runtimeSources || {}])),
    };
  }

  async function refresh(force) {
    const treeResponse = await fetchJson(apiBase + '/tree?t=' + Date.now());
    if (!force && loadedRevision === treeResponse.sha) return false;
    const sessions = sessionFiles(treeResponse.tree || []);
    const candidates = [...sessions.entries()].filter(([, entry]) => isReadable(entry));
    const loaded = [];
    const batchSize = 4;
    for (let offset = 0; offset < candidates.length; offset += batchSize) {
      const batch = await Promise.all(candidates.slice(offset, offset + batchSize).map(async ([sessionId, entry]) => {
        try {
          const previous = activeBundles.find(
            item => item.sessionId === sessionId && item.revision === entry.revision
          );
          return previous || await loadBundle(sessionId, entry.files, entry.revision);
        } catch (error) {
          console.warn(`[OpenHarness local] session skipped: ${sessionId}`, error);
          return null;
        }
      }));
      loaded.push(...batch.filter(Boolean));
    }
    if (loaded.length < 1) {
      throw new Error('Local service did not find any experiment. Run an OpenHarness experiment first.');
    }
    const liveSnapshot = snapshotFrom(
      loaded,
      treeResponse.tree || [],
      treeResponse.sha,
      sessions.size - loaded.length,
    );
    window.OPENHARNESS_SANDBOX = liveSnapshot;
    loadedRevision = treeResponse.sha;
    activeBundles = loaded;
    activeTree = treeResponse.tree || [];
    activeSkippedSessionCount = sessions.size - loaded.length;
    return true;
  }

  function setStatus(kind, message) {
    const target = document.querySelector('.top small');
    if (!target) return;
    target.textContent = message;
    target.dataset.status = kind;
  }

  window.OPENHARNESS_REALTIME_REFRESH = function (force) {
    if (refreshPromise) return refreshPromise;
    const request = (async () => {
      try {
        const changed = await refresh(Boolean(force));
        if (window.OPENHARNESS_SANDBOX?.meta?.commit) {
          const meta = window.OPENHARNESS_SANDBOX.meta;
          setStatus('live', 'Local Live · ' + meta.experimentCount + ' experiments · refresh every ' + Math.round(config.refreshMs / 1000) + 's');
        }
        return changed;
      } catch (error) {
        setStatus('error', '本地实验数据加载失败 · ' + error.message);
        throw error;
      }
    })();
    refreshPromise = request;
    request.finally(() => {
      if (refreshPromise === request) refreshPromise = null;
    }).catch(() => {});
    return request;
  };
  function rebuildSnapshot() {
    window.OPENHARNESS_SANDBOX = snapshotFrom(
      activeBundles,
      activeTree,
      loadedRevision,
      activeSkippedSessionCount,
    );
  }

  window.OPENHARNESS_REALTIME_LOAD_SKILL = async function (sessionId, version) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return false;
    if (Object.prototype.hasOwnProperty.call(bundle.generationSkillSources, version)) return false;
    const key = sessionId + '|' + version + '|' + bundle.revision;
    if (!skillSourcePromises.has(key)) {
      const request = fetchJson(skillSourceUrl(sessionId, version));
      skillSourcePromises.set(key, retryable(skillSourcePromises, key, request));
    }
    bundle.generationSkillSources[version] = await skillSourcePromises.get(key);
    rebuildSnapshot();
    return true;
  };

  window.OPENHARNESS_REALTIME_LOAD_CASE_OVERVIEW = async function (sessionId, caseId) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return false;
    const hasRaw = Object.prototype.hasOwnProperty.call(bundle.rawPackages, caseId);
    const hasQuality = Object.prototype.hasOwnProperty.call(bundle.qualityDocuments, caseId);
    if (hasRaw && hasQuality) return false;
    const results = await Promise.all([
      hasRaw ? bundle.rawPackages[caseId] : loadRawPackage(sessionId, caseId, bundle.revision),
      hasQuality ? bundle.qualityDocuments[caseId] : loadQualityDocument(sessionId, caseId, bundle.revision),
    ]);
    bundle.rawPackages[caseId] = results[0];
    bundle.qualityDocuments[caseId] = results[1];
    rebuildSnapshot();
    return true;
  };

  window.OPENHARNESS_REALTIME_LOAD_CASE_ASSETS = async function (sessionId, caseId) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return false;
    const hasRaw = Object.prototype.hasOwnProperty.call(bundle.rawPackages, caseId);
    const hasMetadata = Object.prototype.hasOwnProperty.call(bundle.metadataDocuments, caseId);
    const hasStructured = Object.prototype.hasOwnProperty.call(bundle.structuredDocuments, caseId);
    const hasQuality = Object.prototype.hasOwnProperty.call(bundle.qualityDocuments, caseId);
    if (hasRaw && hasMetadata && hasStructured && hasQuality) return false;
    const results = await Promise.all([
      hasRaw ? bundle.rawPackages[caseId] : loadRawPackage(sessionId, caseId, bundle.revision),
      hasMetadata ? bundle.metadataDocuments[caseId] : loadMetadataDocument(sessionId, caseId, bundle.revision),
      hasStructured ? bundle.structuredDocuments[caseId] : loadStructuredDocument(sessionId, caseId, bundle.revision),
      hasQuality ? bundle.qualityDocuments[caseId] : loadQualityDocument(sessionId, caseId, bundle.revision),
    ]);
    bundle.rawPackages[caseId] = results[0];
    bundle.metadataDocuments[caseId] = results[1];
    bundle.structuredDocuments[caseId] = results[2];
    bundle.qualityDocuments[caseId] = results[3];
    rebuildSnapshot();
    return true;
  };

  window.OPENHARNESS_REALTIME_LOAD_OUTPUT = async function (sessionId, version, caseId) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return false;
    if (bundle.outputs.some(item => item.version === version && item.case_id === caseId)) return false;
    const output = await loadOutputDocument(sessionId, version, caseId, bundle.revision);
    if (bundle.outputs.some(item => item.version === version && item.case_id === caseId)) return false;
    bundle.outputs.push(output);
    rebuildSnapshot();

    const detailKey = sessionId + '|' + version + '|' + caseId + '|' + bundle.revision;
    if (!judgmentDetailPromises.has(detailKey)) {
      judgmentDetailPromises.set(
        detailKey,
        fetchJson(judgmentDetailUrl(sessionId, version, caseId)).catch(() => null)
      );
    }
    void judgmentDetailPromises.get(detailKey).then(detail => {
      if (!detail) return;
      const summary = [...bundle.judgments].reverse().find(
        item => item.version === version && item.case_id === caseId && !item.invalidated
      );
      if (!summary) return;
      Object.assign(summary, detail);
      rebuildSnapshot();
      if (typeof window.OPENHARNESS_REALTIME_SNAPSHOT_UPDATED === 'function') {
        window.OPENHARNESS_REALTIME_SNAPSHOT_UPDATED();
      }
    });
    return true;
  };

  window.OPENHARNESS_REALTIME_PREFETCH_CASE = function (sessionId, version, caseId) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return;
    void Promise.allSettled([
      loadRawPackage(sessionId, caseId, bundle.revision),
      loadQualityDocument(sessionId, caseId, bundle.revision),
      loadOutputDocument(sessionId, version, caseId, bundle.revision),
    ]);
  };

  window.OPENHARNESS_REALTIME_LOAD_TRACE = async function (sessionId, version, caseId) {
    const bundle = activeBundles.find(item => item.sessionId === sessionId);
    if (!bundle) return false;
    await window.OPENHARNESS_REALTIME_LOAD_OUTPUT(sessionId, version, caseId);
    const output = bundle.outputs.find(item => item.version === version && item.case_id === caseId);
    if (!output || Object.prototype.hasOwnProperty.call(output, 'generationRunTrace')) return false;
    if (!output.generation_id) {
      output.generationRunTrace = null;
      rebuildSnapshot();
      return true;
    }
    const key = sessionId + '|' + version + '|' + caseId + '|' + output.generation_id + '|' + bundle.revision;
    if (!tracePromises.has(key)) {
      const request = fetchJson(
        generationTraceUrl(sessionId, version, caseId, output.generation_id)
      );
      tracePromises.set(key, retryable(tracePromises, key, request));
    }
    output.generationRunTrace = await tracePromises.get(key);
    rebuildSnapshot();
    return true;
  };
  window.OPENHARNESS_REALTIME_CONFIG_RESOLVED = config;
  window.OPENHARNESS_REALTIME_READY = window.OPENHARNESS_REALTIME_REFRESH(true);
})();
