from io import BytesIO

from pypdf import PageObject, PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def text_pdf(
    pages: tuple[str, ...],
    *,
    encrypted: bool = False,
    script: bool = False,
    compressed: bool = False,
) -> bytes:
    writer = PdfWriter()
    for content in pages:
        page = PageObject.create_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        cmap = DecodedStreamObject()
        mappings = "\n".join(
            f"<{char.encode('cp1251').hex()}> <{char.encode('utf-16-be').hex()}>"
            for char in sorted(set(content))
        )
        cmap.set_data(
            (
                "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                "1 begincodespacerange <00> <ff> endcodespacerange\n"
                f"{len(set(content))} beginbfchar\n{mappings}\nendbfchar\n"
                "endcmap end end"
            ).encode()
        )
        font[NameObject("/ToUnicode")] = cmap
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 50 700 Td <" + content.encode("cp1251").hex().encode() + b"> Tj ET"
        )
        page[NameObject("/Contents")] = stream
        stored = writer.add_page(page)
        if compressed:
            stored.compress_content_streams()
    if encrypted:
        writer.encrypt("")
    if script:
        writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
            {
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('PDF_SCRIPT_MUST_NOT_RUN');"),
            }
        )
        _ = writer.add_attachment("untrusted.txt", b"ATTACHMENT_MUST_NOT_EXTRACT")
    output = BytesIO()
    _ = writer.write(output)
    return output.getvalue()


def empty_pdf() -> bytes:
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    )
    result = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)


def image_pdf() -> bytes:
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=612, height=792)
    image = DecodedStreamObject()
    image.set_data(b"\x00\x00\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject({NameObject("/Image"): image}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"q 100 0 0 100 50 600 cm /Image Do Q")
    page[NameObject("/Contents")] = stream
    _ = writer.add_page(page)
    output = BytesIO()
    _ = writer.write(output)
    return output.getvalue()
