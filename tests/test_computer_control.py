import core.tools.computer_control as control


def _hypr_query(command, timeout=2.0):
    if command[1:] == ["monitors", "-j"]:
        return [{"name": "DP-1", "x": 0, "y": 0, "width": 100, "height": 100}], None
    if command[1:] == ["activeworkspace", "-j"]:
        return {"name": "2"}, None
    if command[1:] == ["cursorpos", "-j"]:
        return {"x": 10, "y": 20}, None
    return {"address": "0xabc"}, None


def test_hyprland_context_is_cached(monkeypatch):
    calls = []

    def fake_query(command, timeout=2.0):
        calls.append(command)
        if command[1:] == ["monitors", "-j"]:
            return [{"name": "DP-1", "x": 0, "y": 0, "width": 100, "height": 100}], None
        if command[1:] == ["activeworkspace", "-j"]:
            return {"name": "2"}, None
        if command[1:] == ["cursorpos", "-j"]:
            return {"x": 10, "y": 20}, None
        return {"address": "0xabc"}, None

    monkeypatch.setattr(control, "_compositor", lambda: "hyprland")
    monkeypatch.setattr(control, "_run_query", fake_query)
    control.invalidar_contexto()

    first = control.resolver_contexto()
    second = control.resolver_contexto()

    assert first == second
    assert len(calls) == 4


def test_buscar_ventana_hyprland_matches_class_or_title(monkeypatch):
    monkeypatch.setattr(control, "_compositor", lambda: "hyprland")
    monkeypatch.setattr(
        control,
        "_run_query",
        lambda command: (
            [
                {"address": "0x1", "class": "kitty", "title": "Terminal"},
                {"address": "0x2", "class": "org.vinegarhq.Sober", "title": "Sober"},
            ],
            None,
        ),
    )

    assert control.buscar_ventana("sober") == ("0x2", None)


def test_buscar_ventana_sway_walks_tree(monkeypatch):
    monkeypatch.setattr(control, "_compositor", lambda: "sway")
    monkeypatch.setattr(
        control,
        "_run_query",
        lambda command: (
            {
                "nodes": [
                    {
                        "id": 42,
                        "app_id": "org.vinegarhq.Sober",
                        "name": "Sober",
                        "nodes": [],
                        "floating_nodes": [],
                    }
                ],
                "floating_nodes": [],
            },
            None,
        ),
    )

    assert control.buscar_ventana("vinegar") == ("42", None)


def test_move_retries_and_reports_verified_result(monkeypatch):
    control.invalidar_contexto()
    monkeypatch.setattr(
        control,
        "resolver_contexto",
        lambda force=False: control.DesktopContext("hyprland", "DP-1", "1", "0x1", 0, 0),
    )
    runs = iter([("ydotoold no responde", True), ("OK", False)])
    positions = iter([((10, 20), None)])
    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: next(runs))
    monkeypatch.setattr(control, "consultar_cursor", lambda: next(positions))
    monkeypatch.setattr(control.time, "sleep", lambda _: None)

    message, error = control.mover_mouse(10, 20)

    assert not error
    assert "nivel 1" in message


def test_level_one_retries_before_vlm_fallback(monkeypatch):
    context = control.DesktopContext("hyprland", "DP-1", "1", "0x1", 0, 0)
    calls = {"verify": 0, "fallback": 0, "sleep": []}

    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: ("OK", False))
    monkeypatch.setattr(control.time, "sleep", lambda delay: calls["sleep"].append(delay))

    def verifier():
        calls["verify"] += 1
        return control.Verification(False, 1, "cursor no confirmado", retryable=True)

    def fallback(_message):
        calls["fallback"] += 1
        return True

    result, error = control._verified(
        "test", ["ydotool", "noop"], verifier,
        context=context, retries=3, vlm_fallback=fallback,
    )

    assert not error
    assert result == "OK (diagnóstico nivel 2)"
    assert calls["verify"] == 4
    assert calls["fallback"] == 1
    assert calls["sleep"] == [0.02, 0.05, 0.1]


def test_vlm_fallback_is_not_called_when_level_one_succeeds(monkeypatch):
    calls = {"fallback": 0}
    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: ("OK", False))

    result, error = control._verified(
        "test", ["ydotool", "noop"],
        lambda: control.Verification(True, 1, "confirmado"),
        vlm_fallback=lambda _: calls.__setitem__("fallback", calls["fallback"] + 1),
    )

    assert not error
    assert "nivel 1" in result
    assert calls["fallback"] == 0


def test_action_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: ("ydotoold caído", True))
    result, error = control._verified(
        "test", ["ydotool", "noop"],
        lambda: control.Verification(True, 1, "no debería ejecutarse"),
        retries=1,
    )

    assert error
    assert "Falló test tras 1 reintentos" in result
    assert "ydotoold caído" in result


def test_mantener_tecla_pulsa_espera_y_suelta(monkeypatch):
    calls = []
    monkeypatch.setattr(
        control,
        "resolver_contexto",
        lambda force=False: control.DesktopContext("hyprland", "DP-1", "1", "0x1", 0, 0),
    )
    monkeypatch.setattr(control, "_run", lambda command, **_: calls.append(command) or ("OK", False))
    monkeypatch.setattr(control.time, "sleep", lambda duration: calls.append(("sleep", duration)))

    result = control.mantener_tecla("w", 0.5)

    assert result == ("OK", False)
    assert calls == [
        ["ydotool", "key", "17:1"],
        ("sleep", 0.5),
        ["ydotool", "key", "17:0"],
    ]


def test_mantener_tecla_intenta_soltar_si_key_up_falla(monkeypatch):
    calls = []
    monkeypatch.setattr(
        control,
        "resolver_contexto",
        lambda force=False: control.DesktopContext("hyprland", "DP-1", "1", "0x1", 0, 0),
    )
    runs = iter([("OK", False), ("ydotool falló", True)])
    monkeypatch.setattr(control, "_run", lambda command, **_: calls.append(command) or next(runs))
    monkeypatch.setattr(control.time, "sleep", lambda _duration: None)

    result = control.mantener_tecla("w", 0.1)

    assert result[1] is True
    assert calls == [["ydotool", "key", "17:1"], ["ydotool", "key", "17:0"]]


def test_sway_context_parsing_and_cache(monkeypatch):
    calls = []
    workspaces = [{"name": "web", "num": 2, "focused": True}]
    outputs = [{"name": "DP-1", "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
    tree = {
        "nodes": [{"focused": True, "app_id": "kitty", "nodes": [], "floating_nodes": []}],
        "floating_nodes": [],
    }
    seats = [{"name": "seat0", "capabilities": ["pointer", "keyboard"]}]

    def fake_query(command, timeout=2.0):
        calls.append(command)
        if command[2:] == ["get_workspaces", "-r"]:
            return workspaces, None
        if command[2:] == ["get_outputs", "-r"]:
            return outputs, None
        if command[2:] == ["get_tree", "-r"]:
            return tree, None
        return seats, None

    monkeypatch.setattr(control, "_compositor", lambda: "sway")
    monkeypatch.setattr(control, "_run_query", fake_query)
    control.invalidar_contexto()

    context = control.resolver_contexto()
    assert context.workspace == "web"
    assert context.monitor is None  # Sway no expone cursor en get_seats.
    assert context.focused_window == "kitty"
    assert len(calls) == 4
    control.resolver_contexto()
    assert len(calls) == 4


def test_workspace_change_invalidates_context_but_click_does_not(monkeypatch):
    cached = control.DesktopContext("hyprland", "DP-1", "1", "0x1", 0, 0)
    monkeypatch.setattr(control, "resolver_contexto", lambda force=False: cached)
    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: ("OK", False))
    monkeypatch.setattr(
        control,
        "_verified",
        lambda *args, **kwargs: ("OK (verificado nivel 1)", False),
    )
    control._context_cache = cached

    control.click_mouse()
    assert control._context_cache == cached
    control.cambiar_workspace("2")
    assert control._context_cache is None


def test_explicit_coordinate_action_bypasses_vlm(monkeypatch):
    from core.agent import graph_nodes

    state = {"orden": "mové el mouse a 2700,100"}
    monkeypatch.setattr(
        graph_nodes,
        "iniciar_secuencia",
        lambda: control.DesktopContext("hyprland", "DP-1", "3", "0x1", 0, 0),
    )
    monkeypatch.setattr(graph_nodes, "mover_mouse", lambda x, y: ("OK (verificado nivel 1)", False))
    monkeypatch.setattr(
        graph_nodes,
        "ver_pantalla",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no debe invocarse")),
    )

    result = graph_nodes.node_computer_use(state)

    assert result["computer_use_log"][0]["decision"] == "determinista"
    assert result["computer_use_log"][0]["duracion_ms"] >= 0
    assert "Objetivo cumplido." in result["computer_use_result"]
