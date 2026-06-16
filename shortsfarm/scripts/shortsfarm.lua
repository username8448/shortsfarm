--[[
  shortsfarm.lua - ShortsFarm MPV review script
  ============================================
  Hotkeys
    i  set IN point
    o  set OUT point and save mark
    s  quick 60-second clip from current position
    u  undo last mark
    d  done - video reviewed
    n  skip video
    q  quit (no final status set)

  Each event is written as a JSON line to the file specified by
      --script-opts=sf-marks-file=<path>

  The shutdown handler writes a final "quit" if no done/skip/quit
  was recorded (e.g. user closed the window with the mouse).
--]]

local opts = { ["marks-file"] = "" }
require("mp.options").read_options(opts, "sf")

local marks_file   = opts["marks-file"]
local in_point     = nil
local history      = {}   -- for undo (list of {in, out})
local final_written = false

-- -------------------------------------------------------------------------
-- I/O
-- -------------------------------------------------------------------------
local function write_line(line)
    if marks_file == "" then
        mp.msg.warn("[shortsfarm] marks-file not set - events not saved")
        return
    end
    local f = io.open(marks_file, "a")
    if not f then
        mp.msg.error("[shortsfarm] cannot open: " .. marks_file)
        return
    end
    f:write(line .. "\n")
    f:flush()
    f:close()
end

local function ts()
    return os.date("!%Y-%m-%dT%H:%M:%S")
end

local function pos()
    return mp.get_property_number("time-pos") or 0
end

local function dur()
    return mp.get_property_number("duration") or 0
end

-- -------------------------------------------------------------------------
-- Hotkeys
-- -------------------------------------------------------------------------

-- i: mark IN point
mp.add_forced_key_binding("i", "sf-in", function()
    in_point = pos()
    write_line(string.format('{"event":"set_in","pos":%.6f,"ts":"%s"}', in_point, ts()))
    mp.osd_message(string.format("IN: %.2f s", in_point), 2)
end)

-- o: mark OUT point and save
mp.add_forced_key_binding("o", "sf-out", function()
    if in_point == nil then
        mp.osd_message("Set IN point first  [i]", 2)
        return
    end
    local out_p = pos()
    if out_p <= in_point then
        mp.osd_message("OUT must be after IN point", 2)
        return
    end
    table.insert(history, { ["in"] = in_point, ["out"] = out_p })
    write_line(string.format(
        '{"event":"mark","in":%.6f,"out":%.6f,"rating":null,"label":null,"ts":"%s"}',
        in_point, out_p, ts()
    ))
    mp.osd_message(string.format("Mark: %.2f -> %.2f  (#%d)", in_point, out_p, #history), 2)
    in_point = nil
end)

-- s: quick 60-second clip
mp.add_forced_key_binding("s", "sf-quick", function()
    local p    = pos()
    local d    = dur()
    local clip_end = math.min(p + 60.0, d)
    if clip_end <= p then
        mp.osd_message("Not enough video remaining for a 60 s clip", 2)
        return
    end
    table.insert(history, { ["in"] = p, ["out"] = clip_end })
    write_line(string.format(
        '{"event":"quick_clip","in":%.6f,"out":%.6f,"rating":null,"label":null,"ts":"%s"}',
        p, clip_end, ts()
    ))
    mp.osd_message(string.format("Quick clip: %.2f -> %.2f  (#%d)", p, clip_end, #history), 2)
end)

-- u: undo last mark
mp.add_forced_key_binding("u", "sf-undo", function()
    if #history == 0 then
        mp.osd_message("Nothing to undo", 2)
        return
    end
    local last = history[#history]
    table.remove(history)
    write_line(string.format(
        '{"event":"undo","in":%.6f,"out":%.6f,"ts":"%s"}',
        last["in"], last["out"], ts()
    ))
    mp.osd_message(string.format("Undone mark  (#%d left)", #history), 2)
end)

-- d: done
mp.add_forced_key_binding("d", "sf-done", function()
    write_line(string.format('{"event":"done","ts":"%s"}', ts()))
    final_written = true
    mp.osd_message("Review complete - closing", 1)
    mp.command("quit")
end)

-- n: skip
mp.add_forced_key_binding("n", "sf-skip", function()
    write_line(string.format('{"event":"skip","ts":"%s"}', ts()))
    final_written = true
    mp.osd_message("Skipping - closing", 1)
    mp.command("quit")
end)

-- q: quit without final status (overrides mpv default quit)
mp.add_forced_key_binding("q", "sf-quit", function()
    write_line(string.format('{"event":"quit","ts":"%s"}', ts()))
    final_written = true
    mp.command("quit")
end)

-- Fallback: window close / kill - write quit so Python always sees *something*
mp.register_event("shutdown", function()
    if not final_written then
        write_line(string.format('{"event":"quit","ts":"%s"}', ts()))
    end
end)

mp.msg.info(
    "[shortsfarm] loaded  marks-file=" ..
    (marks_file ~= "" and marks_file or "(not set)")
)
