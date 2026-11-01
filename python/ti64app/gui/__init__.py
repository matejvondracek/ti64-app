"""NiceGUI front end (localhost)."""


def _mirror_markers() -> None:
    """NiceGUI markers (.mark) identify elements for the simulated-user tests; mirror the first one into a
    data-testid attribute so browser tests (Playwright, scripts/gui_screenshots.py) find the same elements."""
    from nicegui.element import Element

    if getattr(Element.mark, "_ti64", False):
        return
    orig = Element.mark

    def mark(self, *markers):
        orig(self, *markers)
        if self._markers:
            self._props["data-testid"] = self._markers[0]
        return self
    mark._ti64 = True
    Element.mark = mark


_mirror_markers()
