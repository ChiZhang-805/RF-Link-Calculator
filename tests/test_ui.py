from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_ui_initial_edit_stale_and_calculate():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert not button(at, "生成完整导出包").disabled
    assert any(m.label == "总增益" and "28.000" in m.value for m in at.metric)
    pin = next(n for n in at.number_input if n.label == "主分析单音 CW 输入功率（dBm）")
    pin.set_value(-50).run()
    assert not at.exception
    assert button(at, "生成完整导出包").disabled
    assert any("W012" in w.value for w in at.warning)
    button(at, "提交并计算").click().run()
    assert not at.exception
    assert not button(at, "生成完整导出包").disabled
    assert any(m.label == "线性输出功率" and "-22.000" in m.value for m in at.metric)


def test_ui_copy_move_delete_and_units():
    at = AppTest.from_file(APP, default_timeout=30).run()
    original = [s["id"] for s in at.session_state.draft["stages"]]
    button(at, "复制器件").click().run()
    assert not at.exception
    copied = at.session_state.draft["stages"]
    assert len(copied) == 4
    assert len({s["id"] for s in copied}) == 4
    button(at, "下移").click().run()
    assert not at.exception
    assert at.session_state.draft["stages"][1]["id"] == original[0]
    unit = next(s for s in at.selectbox if s.label == "工作频率单位")
    before = at.session_state.draft["analysis"]["source_frequency_hz"]
    unit.set_value("GHz").run()
    assert at.session_state.draft["analysis"]["source_frequency_hz"] == before
