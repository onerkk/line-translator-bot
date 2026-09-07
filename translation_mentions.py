"""Shared text-only mention boundaries for detection, protection and validation.

LINE metadata is handled by the event boundary. This fallback preserves existing
mixed-script display names while leaving following sentence words translatable.
"""

import re

_MENTION_STOPWORDS = frozenset("""
a ada agar akan all ambil and anda apa are as at atau awas bagi bagus bahan bahaya bahwa
baik baja banget bantu barang baru batang be before belum benar beres besi besok bilang
bisa bocor boleh book buat buka bukan butuh by can capek cek cepat cuti dalam dan dari
datang dengan di dia dilarang dipahami diperhatikan ditandai do dong for from gak ganti
gimana gudang habis harap harus has hati have if in informasi ini is it itu izin jangan
jika juga kalau kami karena kasih keluar kemarin kerja kipas kirim kita kolom lagi
lantai lembur libur lihat macet maka masih masuk material mau may memahami menggunakan
menjaga mereka mesin minta mohon must nanti no not of oli on only operator or order pada
pakai pake panggil pasang pekerja pelindung pergi perlu pipa please pompa produk
produksi proses pulang rusak saat sakit salah sampai saya sebelum sedang sekarang selalu
selesai semua sesuai setiap should siap stok sudah supaya suruh tapi terima terkait
tersebut that the this tidak to tolong tunggu tutup udah untuk use wajib with without
work worker yang you
""".split())


def extract_mentions(text):
    """Extract textual @mentions without requiring LINE mention metadata.

    Besides native ``@name`` mentions, workers frequently paste the display-name
    form ``@(杰弗)`` / ``@（杰弗）`` as ordinary text.  The old parser did not
    recognize that form, so the name was sent to the translation model and could
    be deleted while only ``@()`` survived.  Keep the exact source substring.
    """
    if not text or not isinstance(text, str):
        return []
    mentions = []

    # Plain-text LINE-style display names: @(杰弗), @（杰弗）, @(John Doe).
    # Require a non-empty body so a damaged literal @() is never treated as a
    # valid identity-bearing mention.
    for match in re.finditer(
        r'@\((?=[^)\r\n]{1,80}\))(?=[^)\r\n]*\S)[^)\r\n]{1,80}\)'
        r'|@（(?=[^）\r\n]{1,80}）)(?=[^）\r\n]*\S)[^）\r\n]{1,80}）',
        text,
    ):
        mention = match.group(0)
        if mention[2:-1].strip() and mention not in mentions:
            mentions.append(mention)

    # English @mentions: grab @word + up to 2 more, trim Indonesian words from end.
    for m in re.finditer(r'@([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\s+([A-Za-z0-9_.-]+))?(?:\s+([A-Za-z0-9_.-]+))?', text):
        first = m.group(1)
        # ``@All`` is a reserved LINE broadcast mention, never a person's
        # multi-word display name.  The generic Latin-name parser used to
        # greedily swallow the next two Indonesian words (for example,
        # ``@All Harap perhatikan``), causing those words to bypass translation
        # and sometimes trip the purity/fidelity guards.  Bound it exactly even
        # when webhook mention metadata is absent or malformed.
        if first.lower() == 'all':
            mention = text[m.start():m.start() + len(first) + 1]
            if mention not in mentions:
                mentions.append(mention)
            continue
        if first.lower() in _MENTION_STOPWORDS:
            continue
        end = m.end(1)
        for index in (2, 3):
            word = m.group(index)
            if not word or word.lower() in _MENTION_STOPWORDS:
                break
            end = m.end(index)
        mention = text[m.start():end]
        if mention not in mentions:
            mentions.append(mention)
    # Chinese/Japanese @mentions, optionally followed by a parenthesized role.
    #
    # LINE metadata frequently marks only the CJK part as the actual mention,
    # while workers append the person's Latin display name as plain text, e.g.
    # ``@蘇比 sobirin``.  Protecting only ``@蘇比`` leaves ``sobirin`` inside the
    # translatable sentence.  The Chinese-target purity gate then (correctly for
    # ordinary source words, incorrectly for this name) rejects it as an
    # untranslated Indonesian word.  Extend the textual mention by up to two
    # conservative lowercase Latin name tokens, but stop before common
    # Indonesian sentence words so ``@阿明 jika ...`` never swallows ``jika``.
    for m in re.finditer(
        r'@[\u4e00-\u9fff\u3040-\u30ff]+(?:\s*[\uff08(][^\uff09)]*[\uff09)])?',
        text,
    ):
        end = m.end()
        tail_end = end
        # A closing name/role parenthesis explicitly ends the mention.
        for _ in range(0 if text[end - 1:end] in {")", "）"} else 2):
            tail = re.match(r'\s+([a-z][a-z0-9_.-]{1,31})', text[tail_end:])
            if not tail:
                break
            token = tail.group(1)
            if token.lower() in _MENTION_STOPWORDS:
                break
            tail_end += tail.end()
        mention = text[m.start():tail_end].rstrip()
        if mention and len(mention) > 1 and mention not in mentions:
            mentions.append(mention)
    # @All
    for m in re.findall(r'@[Aa][Ll][Ll](?![A-Za-z0-9_.-])', text):
        if m not in mentions:
            mentions.append(m)
    return list(dict.fromkeys(mentions))


def mention_spans(text):
    """Return non-overlapping spans in source order, resolving overlaps by length."""
    spans = []
    for literal in sorted(extract_mentions(text), key=len, reverse=True):
        position = 0
        while True:
            start = text.find(literal, position)
            if start < 0:
                break
            end = start + len(literal)
            if not any(start < old_end and end > old_start
                       for old_start, old_end, _ in spans):
                spans.append((start, end, literal))
            position = end
    return sorted(spans)
