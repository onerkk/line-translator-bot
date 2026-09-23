"""Regression for the corrected September 23 work-order notice translation."""

import factory_translation_guard
import factory_knowledge
import app


SOURCE = '''重新宣導一次，以9/23今天的最新資訊為主。
工單資料如果跟掛牌不同，以工單資料為主。
工單上噴漆位置"N"但是旁邊欄位有註記顏色，一律按照上面顏色噴漆。
拋光人員注意：
工單上套環欄位是N就不要套了。
#佳東 #G製程的研磨棒要套環，如果另外有客戶溝通要套環我們後續再新增。'''

VERIFIED_TARGET = '''Disampaikan kembali: gunakan informasi terbaru per hari ini, 9/23, sebagai acuan.
Jika informasi pada work order berbeda dengan TAG, gunakan informasi pada work order sebagai acuan.
Jika posisi pengecatan semprot pada work order tertulis "N", tetapi kolom di sebelahnya mencantumkan warna, tetap lakukan pengecatan semprot sesuai warna yang tertera.
Perhatian bagi petugas polishing:
Jika kolom Cincin Pelindung pada work order tertulis N, jangan dipasang.
#佳東 #G: Batang grinding pada proses G wajib dipasangi Cincin Pelindung. Jika ada komunikasi tambahan dari pelanggan bahwa Cincin Pelindung perlu dipasang, ketentuan itu akan kami tambahkan kemudian.'''


def test_notice_uses_verified_translation_and_rejects_the_bad_tag_sense():
    store = factory_knowledge.get_store()
    cards = store.retrieve(SOURCE, "zh", "id", limit=10)
    assert any(card["id"] == "work_order_tag_color_and_ring_notice_20260923"
               for card in cards)
    assert factory_translation_guard.exact_verified_target(SOURCE, "zh", "id") == VERIFIED_TARGET
    assert app._factory_exact_fallback(SOURCE, "zh", "id") == VERIFIED_TARGET
    contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    assert app.translation_satisfies_semantic_contract(contract, VERIFIED_TARGET)[0]

    report = factory_translation_guard.validate_translation(
        SOURCE, VERIFIED_TARGET.replace("dengan TAG", "dengan papan gantung"), "zh", "id"
    )
    assert not report.ok
    assert any("papan gantung" in issue for issue in report.issues)
