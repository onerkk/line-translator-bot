"""The learning-loop harness scores the real pipeline with a correction hidden vs available."""
import learning_loop_eval as loop


def test_leave_one_out_hides_only_the_evaluated_correction(monkeypatch):
    app, al, casebook = loop._load_app()
    rows = [{"case_id": "correction:7", "id": 7, "source": "A", "target": "B"},
            {"case_id": "correction:8", "id": 8, "source": "C", "target": "D"}]
    monkeypatch.setattr(casebook, "active_corrections_snapshot", lambda *a, **k: rows)
    seen = {}
    def fake_translate(text, src, tgt):
        seen["rows"] = casebook.active_corrections_snapshot(None)
        return "out"
    monkeypatch.setattr(app, "translate", fake_translate)
    case = {"id": "7", "group_id": "", "src_lang": "zh", "tgt_lang": "id", "source": "x", "corrected": "y"}
    assert loop.translate_with_exclusion(app, casebook, case, exclude=True, provider=None) == "out"
    assert [r["id"] for r in seen["rows"]] == [8]
    loop.translate_with_exclusion(app, casebook, case, exclude=False, provider=None)
    assert [r["id"] for r in seen["rows"]] == [7, 8]
    assert casebook.active_corrections_snapshot(None) == rows  # restored


def test_dry_run_report_has_gate_and_categories(tmp_path, monkeypatch):
    app, al, casebook = loop._load_app()
    monkeypatch.setattr(al, "list_corrections", lambda **k: [
        {"id": 1, "src_lang": "zh", "tgt_lang": "id", "src_text": "今天加班", "corrected_translation": "Hari ini lembur.",
         "original_translation": "Hari ini kerja.", "correction_reason": "漏譯加班"}])
    report = loop.run(__import__("argparse").Namespace(provider=None, limit=5, group=None, judge_model="x",
                                                        no_judge=True, dry_run=True, pause=0, out=str(tmp_path / "r")))
    assert report["summary"]["cases"] == 1
    assert report["summary"]["similarity"]["learned"] == 1.0
    assert report["summary"]["gate"] == "pass"
    loop.write_report(report, str(tmp_path / "r"))
    assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Learning loop evaluation")
