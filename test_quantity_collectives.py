import pytest

import factory_quantity_semantics as quantities


@pytest.mark.parametrize("source,target", [
    ("三把都可以生產", "Ketiga bundel sudah boleh diproduksi."),
    ("這兩捆已經確認好了", "Kedua bundel ini sudah diperiksa."),
    ("四批都先保留", "Keempat lot ditahan dahulu."),
    ("五箱都移出", "Pindahkan kelima kotak keluar."),
    ("六根都檢驗", "Periksa keenam batang."),
    ("七包都要領", "Ambil ketujuh bungkus."),
    ("十把都完成", "Kesepuluh bundel sudah selesai."),
    ("十二把都完成", "Kedua belas bundel sudah selesai."),
    ("二十三把都完成", "Kedua puluh tiga bundel sudah selesai."),
    ("三十把都完成", "Ketiga puluh bundel sudah selesai."),
    ("三把都完成", "Ketiga bundel-bundel sudah selesai."),
    ("三把都完成", "Semua 3 bundel sudah selesai."),
    ("三把都完成", "Semua tiga bundel sudah selesai."),
    ("第三把可以生產", "Bundel ketiga boleh diproduksi."),
    ("第3捆可以生產", "Bundel ke-3 boleh diproduksi."),
    ("第二批先保留", "Lot kedua ditahan dahulu."),
    ("第一根先檢查", "Periksa batang pertama dahulu."),
    ("第十二箱移出", "Pindahkan kotak kedua belas keluar."),
])
def test_equivalent_counts_and_ordinals_keep_their_quantity(source, target):
    frame = quantities.build_frame(source)
    assert frame["active"]
    assert quantities.validate_translation(frame, target) == (True, [])


@pytest.mark.parametrize("target", [
    "Kedua bundel sudah boleh diproduksi.",
    "Keempat bundel sudah boleh diproduksi.",
    "Ketiga belas bundel sudah boleh diproduksi.",
    "Ketiga puluh bundel sudah boleh diproduksi.",
    "Kedua puluh tiga bundel sudah boleh diproduksi.",
    "Dua puluh tiga bundel sudah boleh diproduksi.",
    "Tiga puluh bundel sudah boleh diproduksi.",
    "Tiga setengah bundel sudah boleh diproduksi.",
    "1.3 bundel sudah boleh diproduksi.",
    "Ketiga batang sudah boleh diproduksi.",
    "Bundel ketiga sudah boleh diproduksi.",
    "Bundel ke-3 sudah boleh diproduksi.",
    "Semua bundel sudah boleh diproduksi.",
])
def test_changed_count_unit_or_ordinal_cannot_satisfy_three_bundles(target):
    ok, issues = quantities.validate_translation(quantities.build_frame("三把都可以生產"), target)
    assert not ok
    assert "quantity_semantics:atom_missing:q1:3:bundel" in issues


@pytest.mark.parametrize("target", [
    "Ketiga bundel boleh diproduksi.",
    "Tiga bundel boleh diproduksi.",
    "Bundel keempat boleh diproduksi.",
    "Bundel ketiga belas boleh diproduksi.",
    "Bundel ketiga puluh boleh diproduksi.",
])
def test_third_bundle_does_not_become_three_bundles_or_another_ordinal(target):
    frame = quantities.build_frame("第三把可以生產")
    assert frame["atoms"][0]["quantifier"] == "ordinal"
    assert not quantities.validate_translation(frame, target)[0]
