import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import dashboard_api  # noqa: E402


class DashboardDataContractTest(unittest.TestCase):
    def test_custom_sessions_directory_uses_stable_virtual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "external-sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({"versions": [], "cases": []}), encoding="utf-8"
            )
            (session / "outputs.jsonl").write_text("", encoding="utf-8")

            revision, tree = dashboard_api.session_tree(
                Path(tmp) / "unrelated-repository", sessions
            )

            self.assertEqual(64, len(revision))
            self.assertEqual(
                [
                    "app/sessions/exp-1/outputs.jsonl",
                    "app/sessions/exp-1/state.json",
                ],
                [item["path"] for item in tree],
            )

    def test_dataset_resolution_selects_requested_data_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / "data" / "research-report" / "v3" / "data.json"
            expected.parent.mkdir(parents=True)
            expected.write_text("{}", encoding="utf-8")

            resolved = dashboard_api.resolve_dataset_path(
                root, data_version="Data v3"
            )

            self.assertEqual(expected.resolve(), resolved)

    def test_custom_sessions_directory_drives_case_source_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset" / "data.json"
            source = dataset.parent / "inputs" / "brief.txt"
            source.parent.mkdir(parents=True)
            dataset.write_text("{}", encoding="utf-8")
            source.write_text("material", encoding="utf-8")
            sessions = root / "external-sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({
                    "cases": [{
                        "case_id": "case-1",
                        "input_files": [{"source": "inputs/brief.txt"}],
                    }]
                }),
                encoding="utf-8",
            )

            dataset_root, roots = dashboard_api.case_source_roots(
                root, sessions, dataset, "exp-1", "case-1"
            )

            self.assertEqual(dataset.parent.resolve(), dataset_root)
            self.assertEqual([source.resolve()], roots)

    def test_case_metadata_uses_dataset_manifest_and_full_evidence_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset" / "data.json"
            case_root = dataset.parent / "projects" / "case-1"
            source = case_root / "source"
            source.mkdir(parents=True)
            evidence = {
                "schema": "openharness-evidence/v1",
                "case_id": "case-1",
                "items": [{"id": "ev-1", "claim": "complete evidence"}],
                "unresolved": [{"id": "u-1"}],
            }
            (case_root / "evidence_metadata.json").write_text(
                json.dumps(evidence), encoding="utf-8"
            )
            dataset.write_text(
                json.dumps({
                    "cases": [{
                        "case_id": "case-1",
                        "input_files": [{"source": "projects/case-1/source"}],
                    }]
                }),
                encoding="utf-8",
            )
            sessions = root / "sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({
                    "cases": [{
                        "case_id": "case-1",
                        "input_files": [{"source": "stale-v3-path/source"}],
                    }]
                }),
                encoding="utf-8",
            )

            document = dashboard_api.case_metadata_document(
                root, sessions, dataset, "exp-1", "case-1"
            )
            structured = dashboard_api.case_structured_document(
                root, sessions, dataset, "exp-1", "case-1"
            )

            self.assertEqual("case-1", structured["case"]["case_id"])
            self.assertEqual("runtime:data/configured/data.json", structured["source"])
            self.assertEqual(evidence, document["metadata"])
            self.assertEqual("evidence_metadata", document["document_type"])
            self.assertEqual(1, document["evidence_count"])
            self.assertEqual(
                "runtime:data/configured/projects/case-1/evidence_metadata.json", document["source"]
            )

    def test_missing_case_does_not_fall_back_to_another_data_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_dataset = root / "data" / "research-report" / "v1" / "data.json"
            v1_dataset.parent.mkdir(parents=True)
            v1_dataset.write_text(json.dumps({"cases": []}), encoding="utf-8")
            v3_dataset = root / "data" / "research-report" / "v3" / "data.json"
            source = v3_dataset.parent / "projects" / "churn" / "source"
            source.mkdir(parents=True)
            v3_dataset.write_text(json.dumps({"cases": [{
                "case_id": "case-1",
                "input_files": [{"source": "projects/churn/source"}],
            }]}), encoding="utf-8")
            sessions = root / "sessions"
            session = sessions / "v1-experiment"
            session.mkdir(parents=True)
            (session / "state.json").write_text(json.dumps({
                "cases": [{"case_id": "case-1"}],
            }), encoding="utf-8")

            dataset_root, roots = dashboard_api.case_source_roots(
                root, sessions, v1_dataset, "v1-experiment", "case-1"
            )

            self.assertEqual(v1_dataset.parent.resolve(), dataset_root)
            self.assertEqual([], roots)
            with self.assertRaises(FileNotFoundError):
                dashboard_api.case_metadata_document(
                    root, sessions, v1_dataset, "v1-experiment", "case-1"
                )
            with self.assertRaises(FileNotFoundError):
                dashboard_api.case_structured_document(
                    root, sessions, v1_dataset, "v1-experiment", "case-1"
                )
    def test_generation_skill_document_requires_exact_version_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            (sessions / "session-a").mkdir(parents=True)
            artifact = (
                root / "generation_runs" / "_session_skills"
                / "session-a" / "v2" / "abc123" / "research-report"
            )
            (artifact / "references").mkdir(parents=True)
            (artifact / "SKILL.md").write_text(
                "# exact skill v2", encoding="utf-8"
            )
            (artifact / "references" / "instructions.md").write_text(
                "exact instructions v2", encoding="utf-8"
            )
            document = dashboard_api.generation_skill_document(
                root, sessions, "session-a", "v2"
            )
            self.assertEqual("# exact skill v2", document["skill_md"])
            self.assertEqual("exact instructions v2", document["instruction_md"])
            self.assertEqual("session-a", document["session_id"])
            self.assertEqual("v2", document["version"])
            with self.assertRaises(FileNotFoundError):
                dashboard_api.generation_skill_document(
                    root, sessions, "session-a", "v1"
                )

    def test_generation_skill_uses_configured_portable_output_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install-a"
            sessions = root / "runtime-sessions"
            jobs = sessions / "session-a" / "generation_jobs"
            jobs.mkdir(parents=True)
            generation_root = Path(tmp) / "portable-generation-output"
            artifact = (
                generation_root / "_session_skills" / "session-a"
                / "v2" / "hash-a" / "research-report"
            )
            (artifact / "references").mkdir(parents=True)
            (artifact / "SKILL.md").write_text("portable skill", encoding="utf-8")
            (artifact / "references" / "instructions.md").write_text(
                "portable instructions", encoding="utf-8"
            )
            (jobs / "job-a.json").write_text(json.dumps({
                "skill_version": "v2",
                "skill_mode": "session_artifact",
                "skill_ref": (
                    "C:/old/install/custom-output/_session_skills/"
                    "session-a/v2/hash-a/research-report"
                ),
                "created_at": 1,
            }), encoding="utf-8")

            document = dashboard_api.generation_skill_document(
                root,
                sessions,
                "session-a",
                "v2",
                generation_root=generation_root,
            )

            self.assertEqual("portable skill", document["skill_md"])
            self.assertEqual(
                "portable instructions", document["instruction_md"]
            )
    def test_generation_trace_document_requires_exact_generation_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            (sessions / "session-a").mkdir(parents=True)
            run = root / "generation_runs" / "gen-1"
            trace = run / "case-run" / "cases" / "case-a" / "trace"
            (trace / "rounds" / "00_task").mkdir(parents=True)
            (trace.parent / "conversation.md").write_text(
                "real generation conversation", encoding="utf-8"
            )
            (trace / "1_operations.json").write_text(json.dumps([{
                "name": "Read", "status": "success", "round_index": 0,
                "duration_ms": 5, "input": {"path": "a"}, "result": "ok",
            }]), encoding="utf-8")
            (trace / "rounds" / "00_task" / "result.json").write_text(
                json.dumps({
                    "status": "success", "duration_ms": 8,
                    "final_output": "real round output",
                }), encoding="utf-8"
            )
            (run / "generation_result.json").write_text(json.dumps({
                "generation_id": "gen-1",
                "session_id": "session-a",
                "skill_version": "v2",
                "cases": [{
                    "openharness_case_id": "case-a",
                    "attempts": [{
                        "attempt": 1, "status": "success",
                        "wb_run_id": "case-run",
                        "observed_models": ["model-a"],
                    }],
                }],
            }), encoding="utf-8")
            document = dashboard_api.generation_trace_document(
                root, sessions, "session-a", "v2", "case-a", "gen-1"
            )
            self.assertEqual("Read", document["operations"][0]["name"])
            self.assertEqual("real round output", document["rounds"][0]["output"])
            self.assertEqual("real generation conversation", document["conversationText"])
            self.assertTrue(document["source"].startswith("runtime:generation_runs/"))
            with self.assertRaises(FileNotFoundError):
                dashboard_api.generation_trace_document(
                    root, sessions, "session-a", "v1", "case-a", "gen-1"
                )
            with self.assertRaises(FileNotFoundError):
                dashboard_api.generation_trace_document(
                    root, sessions, "session-a", "v2", "case-b", "gen-1"
                )
    def test_loader_handles_partial_tail_and_invalidated_judgments(self):
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")
        self.assertIn("if (index === lines.length - 1) return [];", loader)
        self.assertIn("if (row.invalidated) judgmentMap.delete(key);", loader)
        self.assertIn("/session-summary?session=", loader)
        self.assertNotIn("files.has('judgments.jsonl')", loader)
        self.assertIn("refreshMs: 2 * 1000", loader)
        self.assertIn("const explicitJudge =", loader)
        self.assertIn("judgeBasis", loader)
        self.assertIn("judge === 'v3' ? 'source' : 'groundtruth'", loader)
        self.assertIn("judges: unique(", loader)
        self.assertIn("item.judgeLabel", loader)
        self.assertIn("new Intl.Collator('zh-CN'", loader)
        self.assertNotIn("async function hydrateBundleRawPackages(bundle)", loader)
        self.assertNotIn("async function hydrateBundleMetadata(bundle)", loader)
        self.assertIn("rawPackages: {}", loader)
        self.assertIn("outputs: []", loader)
        self.assertIn("OPENHARNESS_REALTIME_LOAD_OUTPUT", loader)
        self.assertIn("generationSkillSources", loader)
        self.assertIn("artifact?.skill_md || ''", loader)
        self.assertIn("artifact?.instruction_md || ''", loader)
        self.assertIn("/generation-trace?", loader)
        self.assertIn("output.generationRunTrace", loader)
        self.assertNotIn("ownerId === 'sijing'", loader)
        self.assertNotIn("skill.instructions?.prose ||", loader)
        self.assertNotIn("output.generation_trace ||", loader)
        self.assertIn("/skill-source?", loader)
        self.assertNotIn("const outputsText =", loader)
        self.assertIn("OPENHARNESS_REALTIME_LOAD_CASE_ASSETS", loader)


    def test_latest_overview_exposes_judge_filter(self):
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-latest-filter="judge"', adapter)
        self.assertIn("f.judge!==item.judge", adapter)
        self.assertIn('["v3","Judge V3"]', adapter)
        self.assertIn("judge:'all'", page)


    def test_metadata_modal_uses_complete_raw_metadata(self):
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        theme = (
            APP_DIR / "dashboard" / "openharness-theme.css"
        ).read_text(encoding="utf-8")

        self.assertIn("rawMetadata", loader)
        self.assertIn("/case-metadata?session=", loader)
        self.assertIn("metadataDocuments", loader)
        self.assertIn("metadataEvidenceCount", loader)
        self.assertIn("完整原始 Metadata JSON", adapter)
        self.assertIn("evidence_metadata.json", adapter)
        self.assertIn("Metadata 来源", adapter)
        self.assertIn("function evidenceItemsHTML(items)", adapter)
        self.assertIn("metadata-evidence-card", adapter)
        self.assertIn("<dt>source_ref</dt>", adapter)
        self.assertIn("<dt>content</dt>", adapter)
        self.assertIn("Evidence Items", adapter)
        self.assertIn("metadataTopLevelCount(rawMetadata)", adapter)
        self.assertIn("evidenceItemsHTML(evidenceItems)", adapter)
        self.assertIn("JSON.stringify(rawMetadata,null,2)", adapter)
        self.assertIn("metadata-tree-node", adapter)
        self.assertIn("Raw Metadata JSON", theme)
        self.assertIn("Metadata modal: unified high-contrast dark palette", theme)
        self.assertIn(".metadata-modal-head h3{color:#f7f9fc", theme)
        self.assertIn(".metadata-field small{color:#aebccd", theme)
        self.assertIn(".metadata-field b{color:#f2f6fb", theme)
        self.assertIn(".metadata-string{color:#baf3cf", theme)
        self.assertIn("Evidence Items: primary per-case Metadata presentation", theme)
        self.assertIn(".metadata-evidence-content dd", theme)
        self.assertIn("Multi-experiment initial workspace", theme)
        self.assertIn(".experiment-compare-grid", theme)
        self.assertIn(".sandbox-diff-block,", theme)
        self.assertIn(".sandbox-diff-block *", theme)

    def test_generation_trace_does_not_present_judge_trace(self):
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")

        self.assertIn("tracePanelHTML=function", adapter)
        self.assertIn("WB CLI Trace", adapter)
        self.assertNotIn("judge-trace-card", adapter)
        self.assertNotIn("<b>Judge Trace</b>", adapter)
        self.assertIn("generation_runs 中没有与当前实验、版本、generation_id 和 Case 精确对应的 Trace", adapter)
        self.assertNotIn("d.questions[0]||cases[caseIndex][1]", adapter)
        self.assertNotIn("最终报告已生成并进入 Judge", adapter)
        self.assertNotIn("judgeTrace: judgment.judge_trace || null", loader)
    def test_version_compare_hydrates_each_selected_version(self):
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")

        resolver = "const comparedExperiments=isVersionCompareMode()?versionCompareExperiments():state.experiments;"
        self.assertGreaterEqual(page.count(resolver), 2)
        self.assertIn("hydrateCaseTrace(comparedExperiments[+parts[1]],parts[2],+parts[3])", page)
        self.assertIn("single?state.experiments[0]:comparedExperiments[+parts[0]]", page)
        self.assertNotIn("hydrateCaseOutput(state.experiments[+parts[1]]", page)
        self.assertNotIn("single?state.experiments[0]:state.experiments[+parts[0]]", page)
    def test_session_summary_is_compact_cached_and_check_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "meta.json").write_text(
                json.dumps({"experiment_owner": {"id": "owner"}}),
                encoding="utf-8",
            )
            (session / "state.json").write_text(json.dumps({
                "id": "exp-1",
                "rubric": {"dimensions": [{"name": "quality", "checks": []}]},
                "versions": [{"skill": {
                    "version": "v1", "parent_version": None,
                    "instructions": {"prose": "x" * 10000},
                    "few_shots": ["large"],
                }}],
                "cases": [{"case_id": "case-1", "topic": "Case one"}],
            }), encoding="utf-8")
            (session / "check_judgments.jsonl").write_text(
                json.dumps({
                    "version": "v1", "case_id": "case-1",
                    "checks": {"a": 1}, "reasoning": {"a": "ok"},
                    "judge_trace": {"large": "must not bootstrap"},
                }) + "\n",
                encoding="utf-8",
            )
            (session / "judgments.jsonl").write_text(
                json.dumps({"version": "wrong", "case_id": "wrong"}) + "\n",
                encoding="utf-8",
            )
            first = dashboard_api.session_summary_document(sessions, "exp-1")
            second = dashboard_api.session_summary_document(sessions, "exp-1")
            self.assertIs(first, second)
            self.assertEqual("v1", first["state"]["versions"][0]["version"])
            self.assertNotIn("instructions", first["state"]["versions"][0])
            self.assertNotIn("few_shots", first["state"]["versions"][0])
            self.assertEqual("case-1", first["state"]["cases"][0]["case_id"])
            self.assertEqual("check_judgments.jsonl", first["judgment_file"])
            self.assertNotIn("judge_trace", first["judgments"][0])
            self.assertEqual(1, len(first["judgments"]))
            sources = first["runtime_sources"]
            self.assertEqual(
                [
                    "runtime:sessions/exp-1/state.json",
                    "runtime:sessions/exp-1/meta.json",
                ],
                sources["experiment_group"],
            )
            self.assertEqual(
                "runtime:sessions/exp-1/check_judgments.jsonl",
                sources["judge_trace"],
            )
            self.assertNotIn(str(Path(tmp)), json.dumps(sources))
            detail = dashboard_api.case_judgment_document(
                sessions, "exp-1", "v1", "case-1"
            )
            self.assertEqual({"a": "ok"}, detail["reasoning"])
            self.assertNotIn("judge_trace", detail)
            trace_detail = dashboard_api.case_judge_trace_document(
                sessions, "exp-1", "v1", "case-1"
            )
            self.assertEqual(
                {"large": "must not bootstrap"},
                trace_detail["judge_trace"],
            )
            self.assertTrue(
                trace_detail["source"].startswith("runtime:sessions/")
            )
            with self.assertRaises(FileNotFoundError):
                dashboard_api.case_judgment_document(
                    sessions, "exp-1", "v1", "missing"
                )

    def test_output_index_supports_append_rewrite_and_partial_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "outputs.jsonl"
            rows = [
                {"version": "v1", "case_id": "case-1", "report_text": "old"},
                {"version": "v1", "case_id": "case-2", "report_text": "two"},
            ]
            output_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            self.assertEqual(
                "old",
                dashboard_api.case_output_document(
                    output_path, "v1", "case-1"
                )["report_text"],
            )
            with output_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "version": "v1", "case_id": "case-1",
                    "report_text": "new",
                }) + "\n")
                stream.write('{"version":"v1"')
            self.assertEqual(
                "new",
                dashboard_api.case_output_document(
                    output_path, "v1", "case-1"
                )["report_text"],
            )
            with self.assertRaises(FileNotFoundError):
                dashboard_api.case_output_document(
                    output_path, "v1", "case-3"
                )
            with output_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    ',"case_id":"case-3","report_text":"three"}\n'
                )
            self.assertEqual(
                "three",
                dashboard_api.case_output_document(
                    output_path, "v1", "case-3"
                )["report_text"],
            )
            output_path.write_text(json.dumps({
                "version": "v1", "case_id": "case-1",
                "report_text": "rewritten",
            }) + "\n", encoding="utf-8")
            self.assertEqual(
                "rewritten",
                dashboard_api.case_output_document(
                    output_path, "v1", "case-1"
                )["report_text"],
            )
            with self.assertRaises(FileNotFoundError):
                dashboard_api.case_output_document(
                    output_path, "v1", "case-2"
                )
if __name__ == "__main__":
    unittest.main()
