"""Compatibility name for the production FFmpeg renderer."""

from .simple_renderer import RenderResult, SimpleVideoAssembler


class VideoAssembler(SimpleVideoAssembler):
    """The full renderer shares the streaming FFmpeg implementation."""

