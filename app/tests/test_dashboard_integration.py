import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import dashboard_api  # noqa: E402


class DashboardDataContractTest(unittest.TestCase):
    def test_rubric_guide_uses_session_product_and_repository_relative_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({"rubric": {"product": "research_insight"}}),
                encoding="utf-8",
            )
            filename = dashboard_api.RUBRIC_GUIDE_FILES["research_insight"]
            (root / filename).write_text("# rubric guide", encoding="utf-8")

            document = dashboard_api.rubric_guide_document(
                root, sessions, "exp-1"
            )

            self.assertEqual(filename, document["source"])
    def test_custom_sessions_directory_uses_stable_virtual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "external-sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({"versions": [], "cases": []}), encoding="utf-8"
            )

            revision, tree = dashboard_api.session_tree(
                Path(tmp) / "unrelated-repository", sessions
            )

            self.assertEqual(64, len(revision))
            self.assertEqual(
                [
                    "app/sessions/exp-1/state.json",
                ],
                [item["path"] for item in tree],
            )

    def test_dataset_resolution_selects_requested_data_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = (
                root
                / "data"
                / "v3_20260804_real_project_package"
                / "data.json"
            )
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
            source = dataset.parent / "training_data" / "case-1" / "source"
            source.mkdir(parents=True)
            dataset.write_text("{}", encoding="utf-8")
            (source / "brief.txt").write_text("material", encoding="utf-8")
            sessions = root / "external-sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(
                json.dumps({
                    "cases": [{
                        "case_id": "case-1",
                        "input_files": [{"source": "training_data/case-1/source"}],
                    }]
                }),
                encoding="utf-8",
            )

            dataset_root, roots = dashboard_api.case_source_roots(
                root, sessions, dataset, "exp-1", "case-1"
            )

            self.assertEqual(dataset.parent.resolve(), dataset_root)
            self.assertEqual([source.resolve()], roots)

    def test_case_metadata_uses_structured_data_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset" / "data.json"
            case_root = dataset.parent / "training_data" / "case-1"
            source = case_root / "source"
            source.mkdir(parents=True)
            structured_payload = {
                "case_id": "case-1",
                "items": [{"id": "ev-1", "content": "complete evidence"}],
                "nested": {"kept": True},
            }
            (case_root / "structured_data.json").write_text(
                json.dumps(structured_payload), encoding="utf-8"
            )
            dataset.write_text(json.dumps({"cases": [{
                "case_id": "case-1",
                "input_files": [{"source": "training_data/case-1/source"}],
            }]}), encoding="utf-8")
            sessions = root / "sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(json.dumps({
                "cases": [{"case_id": "case-1"}],
            }), encoding="utf-8")

            document = dashboard_api.case_metadata_document(
                root, sessions, dataset, "exp-1", "case-1"
            )
            structured = dashboard_api.case_structured_document(
                root, sessions, dataset, "exp-1", "case-1"
            )
            self.assertEqual(structured_payload, structured["case"])
            self.assertEqual(structured_payload, document["metadata"])
            self.assertEqual("structured_data", document["document_type"])
            self.assertEqual(1, document["evidence_count"])
            self.assertEqual(
                "runtime:data/configured/training_data/case-1/structured_data.json",
                document["source"],
            )
    def test_case_quality_document_parses_score_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset" / "data.json"
            case_root = dataset.parent / "training_data" / "case-1"
            source = case_root / "source"
            source.mkdir(parents=True)
            dataset.write_text(json.dumps({"cases": [{
                "case_id": "case-1",
                "input_files": [{"source": "training_data/case-1/source"}],
            }]}), encoding="utf-8")
            report = (
                "## \u5206\u6570\n\n"
                "| \u6307\u6807 | \u7ed3\u679c |\n|---|---|\n"
                "| \u7efc\u5408\u8d28\u91cf\u5206 | 91.9 / 100 |\n"
                "| \u9057\u6f0f\u8986\u76d6\u5206 | 82.8 / 100 |\n"
                "| \u51b2\u7a81\u4e00\u81f4\u6027\u5206 | 100.0 / 100 |\n"
                "| \u4fe1\u566a\u5206 | 94.1 / 100 |\n"
                "| \u9057\u6f0f\u9879 | 5/29 (17.2%) |\n\n"
                "\u7efc\u5408\u5206 = \u9057\u6f0f\u8986\u76d6\u5206x40% + \u51b2\u7a81\u4e00\u81f4\u6027\u5206x40% + \u4fe1\u566a\u5206x20%\u3002\n"
            )
            (case_root / "case-1_\u6570\u636e\u8d28\u68c0\u62a5\u544a.md").write_text(
                report,
                encoding="utf-8",
            )
            sessions = root / "sessions"
            session = sessions / "exp-1"
            session.mkdir(parents=True)
            (session / "state.json").write_text(json.dumps({
                "cases": [{"case_id": "case-1"}],
            }), encoding="utf-8")

            document = dashboard_api.case_quality_document(
                root, sessions, dataset, "exp-1", "case-1"
            )

            self.assertTrue(document["available"])
            self.assertEqual(91.9, document["overall_score"])
            self.assertEqual(82.8, document["scores"]["\u9057\u6f0f\u8986\u76d6\u5206"])
            self.assertEqual("\u9057\u6f0f\u9879", document["details"][0]["label"])
            self.assertIn("training_data/case-1/", document["source"])

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
    def test_session_summary_uses_generation_skill_versions_and_case_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            session = sessions / "session-a"
            jobs = session / "generation_jobs"
            jobs.mkdir(parents=True)
            (session / "state.json").write_text(json.dumps({
                "versions": [{"version": "v10"}, {"version": "v2"}],
                "cases": [{"case_id": "case-a"}, {"case_id": "case-b"}],
            }), encoding="utf-8")
            for version, generation_id, case_id in (
                ("v10", "gen-10", "case-b"), ("v2", "gen-2", "case-a"),
            ):
                artifact = root / "generation_runs" / "_session_skills" / "session-a" / version / "hash" / "skill"
                (artifact / "references").mkdir(parents=True)
                (artifact / "SKILL.md").write_text(version, encoding="utf-8")
                (artifact / "references" / "instructions.md").write_text(version, encoding="utf-8")
                case_root = root / "generation_runs" / generation_id / "case-run" / "cases" / case_id
                case_root.mkdir(parents=True)
                (root / "generation_runs" / generation_id / "generation_result.json").write_text(json.dumps({
                    "generation_id": generation_id, "session_id": "session-a",
                    "skill_version": version, "cases": [{
                        "openharness_case_id": case_id,
                        "attempts": [{"wb_run_id": "case-run"}],
                    }],
                }), encoding="utf-8")
                (jobs / (generation_id + ".json")).write_text(json.dumps({
                    "generation_id": generation_id, "skill_version": version,
                    "skill_mode": "session_artifact", "skill_ref": str(artifact),
                    "created_at": 1,
                }), encoding="utf-8")
            document = dashboard_api.session_summary_document(
                sessions, "session-a", root
            )
            self.assertEqual(["v2", "v10"], [item["version"] for item in document["state"]["versions"]])
            self.assertEqual(["case-a"], document["state"]["generation_version_cases"]["v2"])
            self.assertEqual(["case-b"], document["state"]["generation_version_cases"]["v10"])

    def test_session_summary_merges_full_run_with_newer_single_case_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            session = sessions / "session-a"
            jobs = session / "generation_jobs"
            jobs.mkdir(parents=True)
            (session / "state.json").write_text(json.dumps({
                "versions": [{"version": "v0"}],
                "cases": [{"case_id": "case-a"}, {"case_id": "case-b"}, {"case_id": "case-c"}],
            }), encoding="utf-8")
            artifact = root / "generation_runs" / "_session_skills" / "session-a" / "v0" / "hash" / "skill"
            (artifact / "references").mkdir(parents=True)
            (artifact / "SKILL.md").write_text("v0", encoding="utf-8")
            (artifact / "references" / "instructions.md").write_text("v0", encoding="utf-8")

            for generation_id, case_ids, created_at in (
                ("gen-full", ["case-a", "case-b", "case-c"], 1),
                ("gen-retry", ["case-b"], 2),
            ):
                result_cases = []
                for case_id in case_ids:
                    case_root = root / "generation_runs" / generation_id / "case-run" / "cases" / case_id
                    case_root.mkdir(parents=True)
                    if generation_id == "gen-full":
                        (case_root / "artifacts").mkdir()
                        (case_root / "artifacts" / "report.md").write_text(case_id, encoding="utf-8")
                    result_cases.append({
                        "openharness_case_id": case_id,
                        "attempts": [{"wb_run_id": "case-run"}],
                    })
                (root / "generation_runs" / generation_id / "generation_result.json").write_text(json.dumps({
                    "generation_id": generation_id,
                    "session_id": "session-a",
                    "skill_version": "v0",
                    "cases": result_cases[:-1] if generation_id == "gen-full" else result_cases,
                }), encoding="utf-8")
                (jobs / (generation_id + ".json")).write_text(json.dumps({
                    "generation_id": generation_id,
                    "skill_version": "v0",
                    "skill_mode": "session_artifact",
                    "skill_ref": str(artifact),
                    "created_at": created_at,
                }), encoding="utf-8")

            document = dashboard_api.session_summary_document(sessions, "session-a", root)
            state = document["state"]
            self.assertEqual(["case-a", "case-b", "case-c"], state["generation_version_cases"]["v0"])
            self.assertEqual("gen-retry", state["generation_version_ids"]["v0"])
            report = dashboard_api.generation_case_report_document(
                root, sessions, "session-a", "v0", "case-a"
            )
            self.assertEqual("gen-full", report["generation_id"])
            self.assertEqual("case-a", report["report_text"])

    def test_generation_trace_document_requires_exact_generation_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "app" / "sessions"
            jobs = sessions / "session-a" / "generation_jobs"
            jobs.mkdir(parents=True)
            run = root / "generation_runs" / "gen-1"
            case_root = run / "case-run" / "cases" / "case-a"
            (case_root / "artifacts").mkdir(parents=True)
            (case_root / "conversation.md").write_text(
                "# real generation conversation", encoding="utf-8"
            )
            (case_root / "artifacts" / "report.md").write_text(
                "# exact report", encoding="utf-8"
            )
            trace_root = case_root / "trace"
            round_root = trace_root / "rounds" / "00_task"
            round_root.mkdir(parents=True)
            (round_root / "request.json").write_text(json.dumps({
                "round_index": 0, "label": "task", "prompt": "real user request",
            }), encoding="utf-8")
            (round_root / "result.json").write_text(json.dumps({
                "round_index": 0, "label": "task", "status": "success",
                "duration_ms": 20, "final_output": "real agent answer",
            }), encoding="utf-8")
            (trace_root / "1_operations.json").write_text(json.dumps([
                {"round_index": 0, "name": "Read", "tool_use_id": "tool-1",
                 "status": "success", "duration_ms": 5, "started_elapsed_ms": 2,
                 "input": {"file_path": str(case_root / "source.txt")},
                 "result": [{"type": "text", "text": "read result"}]},
                {"round_index": 0, "name": "Agent", "tool_use_id": "agent-1",
                 "status": "success", "duration_ms": 8, "started_elapsed_ms": 4,
                 "input": {"description": "delegate evidence check"},
                 "result": "sub-agent result"},
            ]), encoding="utf-8")
            (trace_root / "2_events.jsonl").write_text(json.dumps({
                "round_index": 0, "round_elapsed_ms": 1,
                "event": {"type": "assistant", "parent_tool_use_id": None,
                    "message": {"content": [{"type": "thinking",
                        "thinking": "inspect evidence before writing"}]}},
            }) + "\n", encoding="utf-8")
            (run / "case-run" / "results.json").write_text(json.dumps({
                "summaries": [{"case_id": "case-a", "duration_ms": 30,
                    "rounds": [{"duration_ms": 20,
                        "result_event": {"duration_api_ms": 12},
                        "usage": {"input_tokens": 100, "output_tokens": 25}}]}]
            }), encoding="utf-8")
            (run / "generation_result.json").write_text(json.dumps({
                "generation_id": "gen-1", "session_id": "session-a",
                "skill_version": "v2", "cases": [{
                    "openharness_case_id": "case-a", "attempts": [{
                        "attempt": 1, "status": "success",
                        "wb_run_id": "case-run", "observed_models": ["model-a"],
                    }],
                }],
            }), encoding="utf-8")
            (jobs / "job.json").write_text(json.dumps({
                "generation_id": "gen-1", "skill_version": "v2", "created_at": 1,
            }), encoding="utf-8")
            document = dashboard_api.generation_trace_document(
                root, sessions, "session-a", "v2", "case-a", "gen-1"
            )
            report = dashboard_api.generation_case_report_document(
                root, sessions, "session-a", "v2", "case-a"
            )
            self.assertEqual("# real generation conversation", document["conversationText"])
            self.assertEqual(125, document["metrics"]["usage"]["total_tokens"])
            self.assertEqual(20, document["metrics"]["steps"][0]["durationMs"])
            self.assertEqual(["user", "agent"], [turn["role"] for turn in document["conversation"]])
            self.assertEqual("real user request", document["conversation"][0]["content"])
            self.assertEqual("real agent answer", document["conversation"][1]["content"])
            process_kinds = [item["kind"] for item in document["conversation"][1]["processes"]]
            self.assertEqual(["thinking", "tool", "subagent"], process_kinds)
            self.assertIn("runtime:generation_runs", document["conversation"][1]["processes"][1]["detail"]["input"])
            self.assertNotIn(str(root), json.dumps(document["conversation"]))
            self.assertTrue(document["resultsSource"].endswith("case-run/results.json"))
            self.assertEqual("# exact report", report["report_text"])
            self.assertTrue(report["source"].endswith("artifacts/report.md"))
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
        self.assertIn("displayTerminology", loader)
        self.assertIn("'Human Report'", loader)
        self.assertIn(
            "bundle.state.experiment_user || bundle.meta.experiment_user", loader
        )
        self.assertIn("judges: unique(", loader)
        self.assertIn("item.judgeLabel", loader)
        self.assertIn(
            "'traceability','structure','narrative','insight','coverage','expression'",
            loader,
        )
        self.assertIn(
            "researchBundles.length ? researchBundles : allOrderedBundles",
            loader,
        )
        self.assertIn("hasResearchDimensions(bundle.state)", loader)
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

        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")
        self.assertIn("rawMetadata", loader)
        self.assertIn("/case-metadata?session=", loader)
        self.assertIn("metadataDocuments", loader)
        self.assertIn("metadataEvidenceCount", loader)
        self.assertIn("完整原始 Structured Data JSON", adapter)
        self.assertIn("structured_data.json", adapter)
        self.assertIn("Structured Data 来源", adapter)
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
        self.assertIn("document.querySelector('#metadataClose').onclick=closeMetadata", page)
        self.assertIn("if(e.target.id==='metadataModal')closeMetadata()", page)
        self.assertIn("if(e.key==='Escape')closeMetadata()", page)

    def test_generation_trace_does_not_present_judge_trace(self):
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")
        theme = (
            APP_DIR / "dashboard" / "openharness-theme.css"
        ).read_text(encoding="utf-8")
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")

        self.assertIn("tracePanelHTML=function", adapter)
        self.assertIn("第一级：User / Agent 对话", adapter)
        self.assertIn("第二级 · 执行过程", adapter)
        self.assertIn("第三级 · 执行细节", adapter)
        self.assertIn("trace-process-subagent", theme)
        self.assertIn("trace-execution-group", theme)
        self.assertNotIn("<small>缓存读取</small>", adapter)
        self.assertNotIn("usage.cache_read_input_tokens", adapter)
        self.assertIn("repeat(4,minmax(110px,1fr))", page)
        self.assertIn("报告生成链路", adapter)
        self.assertNotIn("judge-trace-card", adapter)
        self.assertNotIn("<b>Judge Trace</b>", adapter)
        self.assertIn("conversation.md", adapter)
        self.assertIn("tracePanelWithRuntimeHeader", adapter)
        self.assertIn("removeTraceHeaderElement", adapter)
        self.assertIn("headerStart=html.indexOf", adapter)
        self.assertNotIn('[sS]*?</small>', adapter)
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
        self.assertIn("if(opening)await hydrateSkillSource(experiment,parts[1]);", page)
        self.assertIn("Promise.all(compared.map(item=>hydrateSkillSource(item,item.version)))", page)
        self.assertIn("versionCompareExperiments():state.experiments", page)

    def test_skill_diff_preserves_markdown_layout_in_all_compare_modes(self):
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        professional = (
            APP_DIR / "dashboard" / "evaluation-professional-ui.css"
        ).read_text(encoding="utf-8")
        theme = (
            APP_DIR / "dashboard" / "openharness-theme.css"
        ).read_text(encoding="utf-8")

        self.assertIn("Diff keeps the original Markdown structure; only text color changes.", theme)
        self.assertIn("Same-experiment version comparison: preserve Markdown layout.", theme)
        self.assertNotIn("display:block!important;margin:8px 0!important", theme)
        self.assertNotIn("margin-left:-7px!important", professional)
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(encoding="utf-8")

        self.assertIn("dataset.skillDiffSlot", adapter)
        self.assertIn("new Uint16Array", adapter)
        self.assertIn("if(score>=0.28)", adapter)
        self.assertIn("pairs.usedNew.has(index)", adapter)
        self.assertIn("pairs.usedOld.has(index)", adapter)
        self.assertIn("relation==='newer'?'added':'deleted'", adapter)
        self.assertIn("lower[0]||upper[0]||peerEntries[0]", adapter)
        self.assertIn("markdownDiff(skillText,peerSkill?.skillMd,'skill',relation)", adapter)
        self.assertIn("markdownDiff(instructionText,peerSkill?.instructionMd,'instruction',relation)", adapter)
        self.assertIn("mark(item,'deleted'", page)
        self.assertIn("mark(item,'added'", page)
        self.assertIn("mark(oldSegment[pair.oldIndex],'modified'", page)
        self.assertIn("const versionComparison=isVersionCompareMode()", page)
        self.assertIn("state.mode!=='compare'&&!versionComparison", page)
        self.assertIn("entries.slice().sort((a,b)=>a.rank-b.rank)", page)
        self.assertIn("const baseline=comparisonEntries[0]", page)
        self.assertIn("const documentBox=badge.closest('.skill-doc-box')", page)
        self.assertIn("const counts={modified:documentBox.querySelectorAll", page)
        self.assertNotIn("modified:entry.card.querySelectorAll", page)
        self.assertIn("available.includes('v0')?'v0':available[0]", page)
        self.assertIn("state.compareVersions=first&&latest&&first!==latest?[first,latest]", page)
        self.assertNotIn("[available[available.length-1],available[Math.max(0,available.length-2)]]", page)
        self.assertIn("function orderedVersions(e)", page)
        self.assertIn("orderedVersions(e).forEach(v=>", page)
        self.assertIn("orderedVersions(e).map(version=>compareVersionCard", page)
        self.assertNotIn("versions(e).slice().reverse().forEach(v=>", page)
        self.assertNotIn("versions(e).slice().reverse().map(version=>compareVersionCard", page)
        self.assertIn("counts.modified+counts.added+counts.deleted", page)
        self.assertIn("counts.modified", page)
        self.assertIn("counts.added", page)
        self.assertIn("counts.deleted", page)
        self.assertIn("'sandbox-diff-block'", page)
        self.assertIn(".version-compare-card .skill-doc-box .inline-document .skill-diff-added", theme)
        self.assertIn(".version-compare-card .skill-doc-box .inline-document .skill-diff-deleted", theme)
        self.assertIn(".skill-diff-modified", theme)
        self.assertIn(".skill-diff-added", theme)
        self.assertIn(".skill-diff-deleted", theme)
        self.assertIn("querySelectorAll('[data-skill-diff-slot=\"'+slot+'\"]')", page)
        self.assertIn("skill-diff-linked", page)
        self.assertIn(".skill-diff-linked", theme)
        self.assertIn("function revealLinkedSkillDiff(target)", page)
        self.assertIn("box.classList.remove('doc-collapsed')", page)
        self.assertIn("viewport.scrollTo({top:Math.max(0,top),behavior:'smooth'})", page)
        self.assertIn("peers.forEach(revealLinkedSkillDiff)", page)
        self.assertIn(".skill-diff-jump-target", theme)

    def test_case_report_uses_stable_identity_and_fast_overview_loading(self):
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")

        self.assertIn("hydrateCaseOutput(experiment,version,caseIndex),", page)
        self.assertIn("hydrateCaseOverview(experiment,caseIndex),", page)
        self.assertIn("hydrateCaseOverview(state.experiments[0],caseIndex),", page)
        self.assertIn("const caseId=cases[caseIndex]?.[0];", page)
        self.assertIn("remapCaseStateKey", page)
        self.assertIn("OPENHARNESS_REALTIME_PREFETCH_CASE", page)
        self.assertIn("const allOrderedBundles = bundles.slice().sort", loader)
        self.assertNotIn("bundles.sort((a, b)", loader)
        self.assertIn("OPENHARNESS_REALTIME_LOAD_CASE_OVERVIEW", loader)
        self.assertIn("loadOutputDocument(sessionId, version, caseId", loader)
        self.assertIn("void judgmentDetailPromises.get(detailKey)", loader)
        self.assertIn("experimentConditionItem", page)
        self.assertNotIn("optimizerConditionModel", page)
        self.assertNotIn("judgeConditionModel", page)
        self.assertIn("selectedLabel(judges,e.judge)", page)
        self.assertIn("optimizerModel", loader)
        self.assertIn("judgeModel", loader)
        self.assertIn("runtimeModels: summary.runtime_models || {}", loader)
        self.assertIn("bundle.runtimeModels.versions?.[skill.version]", loader)
        self.assertIn("versionModels,", loader)
        self.assertIn("function versionRuntimeModels(experiment)", page)
        self.assertIn("window.OPENHARNESS_SANDBOX?.experiments||[]", page)
        self.assertIn("versionRuntimeModels(state.experiments[0])?.[String(label)]", page)
        self.assertIn("skill-runtime-meta", page)
        self.assertIn("label=safeInlineText(label)+'<span class=\"skill-runtime-meta\">", page)
        self.assertIn("tag='';", page)
        self.assertIn('<th class="node-head">Skill 版本</th>', page)
        self.assertNotIn('<th class="node-head">层级</th>', page)
        self.assertIn("grid-template-columns:minmax(200px,max-content) minmax(176px,max-content)", page)
        self.assertIn("min-width:388px;margin-left:8px", page)
        self.assertIn("max-width:none;overflow:visible;text-overflow:clip", page)
        self.assertIn("definition=(window.OPENHARNESS_SANDBOX?.experiments||[]).find", page)
        self.assertIn("versionModels:definition.versionModels||{}", page)
        self.assertNotIn("modelCell(runtime.optimizerModel", page)
        self.assertNotIn("modelCell(runtime.judgeModel", page)
        self.assertNotIn("['optimizer-model','Optimizer \\u6a21\\u578b']", page)
        self.assertEqual(1, page.count("function singleTable()"))
        self.assertIn("bundle.runtimeModels.optimizer?.model || null", loader)
        self.assertIn("bundle.runtimeModels.judge?.model || null", loader)
        self.assertNotIn("explicitOptimizer.model || bundle.state.optimizer_runtime", loader)
        self.assertNotIn("latestJudgeRuntime.model", loader)
        self.assertNotIn("experimentConditionItem('User'", page)
        self.assertIn("function experimentConditionItem(label,value)", page)
        self.assertIn("function renderConfig(){const card=document.querySelector('.config')", page)
        self.assertIn("card.hidden=true;box.innerHTML=''", page)
        self.assertIn("function compareExperimentConditions(e)", page)
        self.assertIn("['会话',selectedLabel(sessions,e.session)]", page)
        self.assertIn("['Data 类型',selectedLabel(dataTypes,e.data)]", page)
        self.assertIn("['Optimizer 类型',selectedLabel(optimizers,e.optimizer)]", page)
        self.assertIn("['Judge 类型',selectedLabel(judges,e.judge)]", page)
        self.assertIn('class="compare-experiment-remove" data-remove-experiment="', page)
        self.assertIn("const removeExperiment=e.target.closest('[data-remove-experiment]')", page)
        self.assertIn(".config[hidden]{display:none!important}", page)
        self.assertIn(".compare-experiment-conditions{display:grid", page)
        self.assertNotIn('<div class="compare-experiment-meta"><span>', page)
        self.assertNotIn("model===null?value:value+", page)
        self.assertIn("<b title=\"'+value+'\">'+value+'</b>", page)
        self.assertNotIn("return e.optimizerModel||", page)
        self.assertNotIn("模型未记录", page)
        self.assertNotIn("e.optimizer==='switch_search'?", page)
        self.assertNotIn("<em title=", page)

    def test_report_evaluation_kpi_colors_are_semantic(self):
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")
        theme = (
            APP_DIR / "dashboard" / "openharness-theme.css"
        ).read_text(encoding="utf-8")

        self.assertIn('class="evaluation-total-value"', page)
        self.assertIn('class="evaluation-redline-value redline-detail-target ', page)
        self.assertIn(
            ".eval-kpis .evaluation-total-value{color:var(--green)!important}", theme
        )
        self.assertIn(
            ".eval-kpis .evaluation-redline-value{color:var(--red)!important}", theme
        )

    def test_case_quality_score_is_lazy_loaded_and_rendered(self):
        loader = (
            APP_DIR / "dashboard" / "local-realtime-loader.js"
        ).read_text(encoding="utf-8")
        adapter = (
            APP_DIR / "dashboard" / "sandbox-adapter.js"
        ).read_text(encoding="utf-8")
        page = (
            APP_DIR / "dashboard" / "experiment-evaluation-tree.html"
        ).read_text(encoding="utf-8")
        theme = (
            APP_DIR / "dashboard" / "openharness-theme.css"
        ).read_text(encoding="utf-8")

        self.assertIn("/case-quality?session=", loader)
        self.assertIn("qualityDocuments", loader)
        self.assertIn("loadQualityDocument", loader)
        self.assertIn("data-open-quality", adapter)
        self.assertIn("openQualityModal", adapter)
        self.assertIn("window.openQualityModal=openQualityModal", adapter)
        self.assertIn("quality-overall-card", adapter)
        self.assertIn("quality-subscore-section", adapter)
        self.assertIn("quality-subscore-row", theme)
        self.assertIn("data-open-quality", page)
        self.assertIn("await hydrateCaseAssets(experiment,caseIndex)", page)
        self.assertIn(".data-quality-button", theme)
        self.assertIn(".quality-score-card", theme)

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
                "experiment_user": "Zoe",
                "rubric": {"dimensions": [{"name": "quality", "checks": []}]},
                "versions": [{"skill": {
                    "version": "v1", "parent_version": None,
                    "instructions": {"prose": "x" * 10000},
                    "few_shots": ["large"],
                }}],
                "cases": [{"case_id": "case-1", "topic": "Case one"}],
                "optimizer_mode": "llm_rewrite",
                "opt_history": [
                    {"candidate": "v1", "llm_backend": "workbuddy",
                     "model": "optimizer-model"},
                ],
            }), encoding="utf-8")
            (session / "events.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "ts": 100, "type": "version_proposed",
                        "payload": {
                            "version": "v1", "llm_backend": "workbuddy",
                            "model": "events-optimizer-model",
                        },
                    }),
                    json.dumps({
                        "ts": 101, "type": "run_judge_batch",
                        "payload": {
                            "version": "v1", "case_ids": ["case-1"],
                            "llm_backend": "workbuddy", "model": "events-judge-model",
                        },
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (session / "check_judgments.jsonl").write_text(
                json.dumps({
                    "version": "v1", "case_id": "case-1",
                    "checks": {"a": 1}, "reasoning": {"a": "ok"},
                    "llm_backend": "workbuddy", "model": "judge-model",
                    "ts": 123,
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
            self.assertEqual("Zoe", first["state"]["experiment_user"])
            self.assertEqual("check_judgments.jsonl", first["judgment_file"])
            self.assertNotIn("judge_trace", first["judgments"][0])
            self.assertEqual(
                "optimizer-model", first["state"]["optimizer_runtime"]["model"]
            )
            self.assertEqual("judge-model", first["judgments"][0]["model"])
            self.assertEqual("workbuddy", first["judgments"][0]["llm_backend"])
            self.assertEqual(1, len(first["judgments"]))
            self.assertEqual(
                "optimizer-model", first["runtime_models"]["optimizer"]["model"]
            )
            self.assertEqual(
                "judge-model", first["runtime_models"]["judge"]["model"]
            )
            self.assertEqual("check_judgments.jsonl", first["runtime_models"]["judge"]["source"])
            version_runtime = first["runtime_models"]["versions"]["v1"]
            self.assertEqual(
                "optimizer-model", version_runtime["optimizer"]["model"]
            )
            self.assertEqual(
                "judge-model", version_runtime["judge"]["model"]
            )
            self.assertEqual("case-1", version_runtime["judge"]["case_id"])
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
            self.assertEqual(
                {
                    "optimizer": "runtime:sessions/exp-1/state.json#opt_history",
                    "judge": "runtime:sessions/exp-1/check_judgments.jsonl",
                    "compatibility_fallback": "runtime:sessions/exp-1/events.jsonl",
                },
                sources["runtime_models"],
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
    def test_case_panel_widths_survive_dashboard_rerender(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("caseLayoutWidths:new Map()", page)
        self.assertIn("function rememberCaseLayoutWidth", page)
        self.assertIn("function restoreCaseLayoutWidths", page)
        self.assertIn("renderTree();restoreCaseLayoutWidths();", page)
        self.assertIn("rememberCaseLayoutWidth(drawer,'evaluation',next)", page)
        self.assertIn("rememberCaseLayoutWidth(drawer,'report',next)", page)

    def test_multi_experiment_conditions_stay_on_one_row(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "grid-template-columns:repeat(4,minmax(0,1fr))!important", theme
        )
        self.assertIn("white-space:nowrap!important", theme)
        self.assertIn("display:flex;min-width:0;align-items:center", theme)
        self.assertIn("font-size:15px!important", theme)
        self.assertIn("font-weight:400!important", theme)
        self.assertIn("compare-condition-item small:after", theme)

    def test_compare_trace_button_is_borderless_black_and_white(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".version-case-report-drawer .report-side-trace-toggle{", theme)
        self.assertIn("border:0!important;border-radius:6px!important", theme)
        self.assertIn("background:#12161c!important;color:#fff!important", theme)
        self.assertIn("color:#fff!important;box-shadow:none!important", theme)
        self.assertNotIn("background:#1f4872!important", theme)
        self.assertIn(".report-side-trace-toggle:hover{", theme)
        self.assertIn(".report-side-trace-toggle.open{", theme)

    def test_case_list_subtitle_has_no_decorative_container(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("border:0!important;border-radius:0!important", theme)
        self.assertIn("background:transparent!important", theme)
        self.assertIn("color:#dce5ef!important;text-shadow:none!important", theme)

    def test_rubric_dimension_score_uses_the_detail_score_column(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("grid-template-columns:minmax(0,1.2fr) 76px minmax(0,1.35fr)", theme)
        self.assertIn(".eval-group h4>b{grid-column:2", theme)
        self.assertIn(".version-case-report-drawer .eval-group h4{", theme)
        self.assertIn("grid-template-columns:minmax(0,1fr) 76px", theme)

    def test_version_select_has_no_outer_frame_and_matches_batch_buttons(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".version-compare-toolbar .version-compare-picks label{", theme)
        self.assertIn("padding:0!important;border:0!important", theme)
        self.assertIn(".version-compare-toolbar .version-compare-picks>.batch{", theme)
        self.assertIn("min-height:31px", theme)

    def test_evaluation_title_itself_can_always_reach_the_viewport_top(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("function scrollTitleBarToTop(element)", page)
        self.assertIn("dashboardTitleScrollSpacer", page)
        self.assertIn("top+viewport-root.scrollHeight+2", page)
        self.assertIn("root.scrollTo({top,behavior:'smooth'})", page)
        self.assertEqual(6, page.count("scrollTitleBarToTop(")-1)
        self.assertIn("scrollTitleBarToTop(alignVersionCompare)", page)
        self.assertIn("scrollTitleBarToTop(event.currentTarget)", page)
        self.assertIn("!e.target.closest('button,select,label')", page)

    def test_single_report_drawer_nearly_fills_the_visible_viewport(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".table-card.single-report-expanded .single-case-drawer{", theme)
        self.assertIn("height:calc(100vh - 52px)!important", theme)
        self.assertIn("height:calc(var(--aligned-viewport-height,100dvh) - 52px)!important", theme)
        self.assertIn("height:auto!important;min-height:0!important", theme)

    def test_opened_case_row_aligns_to_top_and_compare_report_fills_viewport(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("function scrollOpenedCaseToTop(key)", page)
        self.assertIn("toggle.closest('tr.level-1')", page)
        self.assertIn("toggle.closest('.compare-case-summary')", page)
        self.assertIn("scrollOpenedCaseToTop(key);", page)
        self.assertIn("]).then(()=>scrollOpenedCaseToTop(key));", page)
        self.assertIn(".version-case-report-drawer{", theme)
        self.assertIn("height:calc(100vh - 52px)!important", theme)
        self.assertIn(".compare-version-stack:has(.version-case-report-drawer)", theme)
        self.assertIn("overflow:visible!important", theme)

    def test_opened_skill_row_aligns_to_top_and_skill_fills_viewport(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("function scrollOpenedSkillToTop(key)", page)
        self.assertIn("toggle.closest('tr.level-0')", page)
        self.assertIn("toggle.closest('.version-panel .compare-experiment-head,.compare-version-summary')", page)
        self.assertEqual(3, page.count("scrollOpenedSkillToTop(key)"))
        self.assertIn(".single-skill-drawer,", theme)
        self.assertIn("height:calc(100vh - 52px)!important", theme)
        self.assertIn("height:calc(var(--aligned-viewport-height,100dvh) - 52px)!important", theme)
        self.assertIn("grid-auto-rows:minmax(0,1fr);overflow:auto", theme)

    def test_skill_documents_wrap_without_horizontal_scrolling(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("overflow-x:hidden!important;overflow-y:auto!important", theme)
        self.assertIn("white-space:normal;overflow-wrap:anywhere;word-break:break-word", theme)
        self.assertIn("white-space:pre-wrap!important;overflow-x:hidden!important", theme)
        self.assertIn("table-layout:fixed", theme)
        self.assertIn("td{white-space:normal!important", theme)

    def test_compare_cards_scroll_open_items_independently_on_one_horizontal_line(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("function scrollComparisonCardItemToTop(target)", page)
        self.assertIn("target.closest('.version-panel-scroll,.compare-version-stack')", page)
        self.assertIn("scroller.scrollTo({top:Math.max(0,top),behavior:'smooth'})", page)
        self.assertIn("scrollTitleBarToTop(scroller)", page)
        self.assertEqual(2, page.count("!scrollComparisonCardItemToTop(target)"))
        self.assertIn("Comparison cards keep independent vertical positions", theme)
        self.assertIn("overflow-x:hidden!important;overflow-y:auto!important;overscroll-behavior:contain", theme)
        self.assertIn("max-height:calc(var(--aligned-viewport-height,100dvh) - 52px)!important", theme)

    def test_multi_experiment_case_reuses_version_compare_layout(self):
        page = (APP_DIR / "dashboard" / "experiment-evaluation-tree.html").read_text(
            encoding="utf-8"
        )
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("--multi-skill-frame-height:1200px", theme)
        self.assertNotIn("--multi-expanded-frame-height", theme)
        self.assertNotIn(".table-card.multi-experiment-layout .compare-version-drawer>.compare-case-list{", theme)
        self.assertNotIn(".table-card.multi-experiment-layout .version-case-report-drawer{", theme)
        self.assertIn("layoutClass='',evaluationExperimentIndex=isVersionCompareMode()?0:experimentIndex", page)
        self.assertIn(".version-case-report-drawer{\n  height:calc(100vh - 52px)!important", theme)

    def test_compare_generation_trace_table_headers_use_dark_surface(self):
        theme = (APP_DIR / "dashboard" / "openharness-theme.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".version-case-report-drawer .trace-panel .inline-document th,", theme)
        self.assertIn(".version-case-report-drawer .trace-panel .md-table th{", theme)
        self.assertIn("background:#171f29!important;color:#e4eaf2!important", theme)

if __name__ == "__main__":
    unittest.main()
