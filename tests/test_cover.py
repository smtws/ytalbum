"""Square covers: crop only padding, never picture content."""

import io

import pytest
from PIL import Image, ImageDraw

from ytalbum.cover import square_if_padded


def jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def pillarboxed(art_width=680, shift=0, bg=(0, 0, 0)) -> Image.Image:
    img = Image.new("RGB", (1280, 720), bg)
    x = 640 - art_width // 2 + shift
    ImageDraw.Draw(img).rectangle((x, 18, x + art_width, 702), fill=(180, 140, 60))
    return img


def test_pillarboxed_thumbnail_becomes_the_square_art():
    out = square_if_padded(jpeg(pillarboxed()))
    assert out and size(out) == (720, 720)
    # the art is inside the crop: its edges are still there, the bars are gone
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((360, 360))[0] > 150
    assert img.getpixel((5, 360))[0] < 30


def test_art_fading_into_the_background_stays_centred():
    img = pillarboxed()
    ImageDraw.Draw(img).rectangle((900, 18, 982, 702), fill=(12, 12, 12))  # dark right edge of the art
    out = Image.open(io.BytesIO(square_if_padded(jpeg(img)))).convert("RGB")
    assert out.getpixel((10, 360))[0] < 30 and out.getpixel((709, 360))[0] < 30  # equal black margins


def test_off_centre_art_is_followed():
    out = square_if_padded(jpeg(pillarboxed(art_width=600, shift=250)))
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((360, 360))[0] > 150


def test_light_background_bars_count_too():
    assert size(square_if_padded(jpeg(pillarboxed(bg=(240, 240, 240))))) == (720, 720)


@pytest.mark.parametrize(
    "img",
    [
        pillarboxed(art_width=900),  # art wider than a square: cropping would cut it
        Image.effect_noise((1280, 720), 60).convert("RGB"),  # a real 16:9 picture
        Image.new("RGB", (500, 500), (10, 20, 30)),  # already square
        Image.new("RGB", (1280, 720), (0, 0, 0)),  # blank
    ],
)
def test_content_is_never_cut(img):
    assert square_if_padded(jpeg(img)) is None


def test_non_uniform_corners_mean_no_padding():
    img = pillarboxed()
    ImageDraw.Draw(img).rectangle((0, 0, 40, 40), fill=(255, 0, 0))
    assert square_if_padded(jpeg(img)) is None


def test_garbage_is_ignored():
    assert square_if_padded(b"<html>consent</html>") is None
