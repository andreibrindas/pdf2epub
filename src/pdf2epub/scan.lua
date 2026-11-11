-- pdf2epub filter for scanned books (EPUB, DOCX, plain text). Run by scanbuild.py.
--
-- 1. Page markers <!-- p.N --> become empty page-break anchors id="p-N" (EPUB: epub:type
--    "pagebreak" with aria-label N, so the build can write the page list). A marker on its own
--    line is attached to the start of the next paragraph or heading; an inline one stays where
--    it is. Markers of unnumbered pages, <!-- x.N -->, are dropped. The build ends every part
--    with <!-- pdf2epub:part-end -->, where anchors still waiting (a blank last page, the notes
--    pages of a chapter) are put in an empty div, so they never drift into the next chapter.
-- 2. Page numbers in ::: index divs link to those anchors (arabic, or roman as printed). In a
--    ::: {.index .scripture} div only numbers after "p." / "pp." are pages (the others are
--    chapters and verses), until the next book name, verse or ";".
--    Notes printed at the end of a chapter keep their pages' markers inside the notes (the
--    build moves an own-line marker into the definition after it), and each part's notes are
--    output before the anchors still waiting at its end.
-- 3. Chapter headings `# Title {label="3"}` get the label shown above the title. In the
--    reader's contents list the entry reads "3. Title" or "Chapter One: Title".
-- 4. EPUB and plain text: each chapter's notes become a visible list at the end of the
--    chapter, labelled as printed (the build puts the printed label in a leading
--    [N]{.fnlabel} span of each note). DOCX keeps real Word footnotes.

local epub = FORMAT:match('epub') ~= nil
local endnotes = epub or FORMAT == 'plain'
local anchors = {}
local notes_title = 'Notes'

local function html(s) return pandoc.RawInline('html', s) end

local function esc(s)
  return (s:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;'):gsub('"', '&quot;'))
end

-- 'p', '7' | 'x', '3' | nil
local function marker(raw)
  if raw.format ~= 'html' then return nil end
  return raw.text:match('^%s*<!%-%-%s*([px])%.(%w+)[%a ]*%-%->%s*$')
end

local function is_part_end(raw)
  return raw.format == 'html' and raw.text:match('^%s*<!%-%-%s*pdf2epub:part%-end%s*%-%->%s*$') ~= nil
end

local function anchor(lab)
  anchors[lab] = true
  if epub then
    return pandoc.Span({}, pandoc.Attr('p-' .. lab, {},
      { { 'epub:type', 'pagebreak' }, { 'role', 'doc-pagebreak' }, { 'aria-label', lab } }))
  end
  return pandoc.Span({}, pandoc.Attr('p-' .. lab))
end

-- 1 ---------------------------------------------------------------------------
local function page_anchors(doc)
  local pending = {}
  local function take(el)
    if #pending == 0 then return nil end
    for i = #pending, 1, -1 do el.content:insert(1, pending[i]) end
    pending = {}
    return el
  end
  doc = doc:walk {
    traverse = 'topdown',
    RawBlock = function(el)
      if is_part_end(el) then
        local div = pandoc.Div(#pending > 0 and { pandoc.Plain(pending) } or {}, { class = 'part-end' })
        pending = {}
        return div
      end
      local k, lab = marker(el)
      if k == 'p' then
        table.insert(pending, anchor(lab))
        return {}
      elseif k == 'x' then
        return {}
      end
    end,
    RawInline = function(el)
      local k, lab = marker(el)
      if k == 'p' then return anchor(lab) end
      if k == 'x' then return {} end
    end,
    Para = take,
    Plain = take,
    Header = take,
    LineBlock = function(el)
      if #pending == 0 then return nil end
      for i = #pending, 1, -1 do el.content[1]:insert(1, pending[i]) end
      pending = {}
      return el
    end,
  }
  if #pending > 0 then doc.blocks:insert(pandoc.Div { pandoc.Plain(pending) }) end
  return doc
end

-- 2 ---------------------------------------------------------------------------
local ROMAN = '[ivxlcdmIVXLCDM]+'

-- '(79-81),' -> '(', '79', '-81', '),' ; the page is decimal or a roman folio with an anchor
local function page_ref(t)
  local pre, num, rest = t:match('^([%(%[]*)(%d+)(.*)$')
  if not num then
    pre, num, rest = t:match('^([%(%[]*)(' .. ROMAN .. ')(%p*)$')
    if not num then
      pre, num, rest = t:match('^([%(%[]*)(' .. ROMAN .. ')([%-–].*)$')
    end
  end
  if not (num and anchors[num]) then return nil end
  local digits = num:match('^%d') and '%d+' or ROMAN
  for _, dash in ipairs { '%-', '–' } do
    local range, tail = rest:match('^(' .. dash .. digits .. ')(.*)$')
    if range then return pre, num, range, tail end
  end
  return pre, num, '', rest
end

local function link_numbers(para, scripture)
  local out = pandoc.Inlines {}
  local active = not scripture
  for _, il in ipairs(para.content) do
    local done = false
    if il.t == 'Str' then
      local t = il.text
      if scripture then
        -- pandoc's smart typography glues "p. 79" into one Str with a no-break space
        local pp, after = t:match('^(pp?%.)\194\160(.*)$')
        if pp then
          out:insert(pandoc.Str(pp .. '\194\160'))
          t, active = after, true
        elseif t == 'p.' or t == 'pp.' then active = true
        elseif t:find(':') or t:match('^%u') then active = false end   -- a verse, a book name
      end
      local pre, num, range, tail
      if active then pre, num, range, tail = page_ref(t) end
      if num then
        if pre ~= '' then out:insert(pandoc.Str(pre)) end
        out:insert(pandoc.Link(num .. range, '#p-' .. num, '', { class = 'pg' }))
        if tail ~= '' then out:insert(pandoc.Str(tail)) end
        done = true
      end
      if scripture and t:match(';$') then active = false end   -- the next reference starts
    elseif scripture and (il.t == 'Emph' or il.t == 'Strong' or il.t == 'SmallCaps') then
      active = false   -- a book name set in italics
    end
    if not done then out:insert(il) end
  end
  para.content = out
  return para
end

local function index_links(doc)
  return doc:walk {
    Div = function(div)
      if not div.classes:includes('index') then return nil end
      local scripture = div.classes:includes('scripture')
      return div:walk { Para = function(p) return link_numbers(p, scripture) end }
    end,
  }
end

-- 3 ---------------------------------------------------------------------------
local function labelled(h)
  local lab = h.attributes.label
  if not lab then return { h } end
  h.attributes.label = nil
  -- after the heading's own page anchors, so the page starts before the label
  local pos = 1
  while pos <= #h.content and h.content[pos].t == 'Span' and h.content[pos].identifier:match('^p%-') do
    pos = pos + 1
  end
  if epub then
    -- label on its own line above the title; the separator is hidden by CSS in the
    -- book but keeps "Chapter One: Title" readable in the reader's contents list
    local sep = lab:match('^%d+$') and '. ' or ': '
    h.content:insert(pos, pandoc.Span(pandoc.Str(sep), { class = 'label-sep' }))
    h.content:insert(pos, pandoc.Span(pandoc.Str(lab), { class = 'chapter-label' }))
    return { h }
  end
  if FORMAT == 'docx' then
    -- Heading 1 starts a new page in the reference doc, so keep the label inside it
    h.content:insert(pos, pandoc.LineBreak())
    h.content:insert(pos, pandoc.Str(lab))
    return { h }
  end
  return { pandoc.Para { pandoc.Str(lab) }, h }
end

-- 4 ---------------------------------------------------------------------------

-- the printed label from the note's leading [N]{.fnlabel} span, which is removed
local function take_label(blocks)
  local b = blocks[1]
  if not b or (b.t ~= 'Para' and b.t ~= 'Plain') then return nil end
  local first = b.content[1]
  if not (first and first.t == 'Span' and first.classes:includes('fnlabel')) then return nil end
  local lab = pandoc.utils.stringify(first)
  b.content:remove(1)
  local nx = b.content[1]
  if nx and (nx.t == 'Space' or nx.t == 'SoftBreak') then b.content:remove(1) end
  return lab
end

local function notes_section(ch, notes)
  if not epub then
    local out = { pandoc.Para { pandoc.Str(notes_title) } }
    for _, nt in ipairs(notes) do
      local mark = pandoc.Str('[' .. nt.label .. '] ')
      local first = nt.content[1]
      if first and (first.t == 'Para' or first.t == 'Plain') then
        first.content:insert(1, mark)
      else
        table.insert(out, pandoc.Para { mark })
      end
      for _, b in ipairs(nt.content) do table.insert(out, b) end
    end
    return out
  end
  local out = {
    pandoc.RawBlock('html', '<section class="notes" epub:type="endnotes" role="doc-endnotes">'),
    pandoc.RawBlock('html', '<h2 class="notes-title">' .. esc(notes_title) .. '</h2>'),
  }
  for n, nt in ipairs(notes) do
    local back = html(string.format(
      '<a class="note-back" href="#c%d-r%d" role="doc-backlink"><sup>%s</sup></a>', ch, n, esc(nt.label)))
    local blocks = nt.content
    local first = blocks[1]
    if first and (first.t == 'Para' or first.t == 'Plain') then
      first.content:insert(1, pandoc.Space())
      first.content:insert(1, back)
    else
      blocks:insert(1, pandoc.Para { back })
    end
    table.insert(out, pandoc.RawBlock('html', string.format(
      '<div class="note" id="c%d-n%d" epub:type="endnote">', ch, n)))
    for _, b in ipairs(blocks) do table.insert(out, b) end
    table.insert(out, pandoc.RawBlock('html', '</div>'))
  end
  table.insert(out, pandoc.RawBlock('html', '</section>'))
  return out
end

local function chapters(doc)
  local blocks, notes, ch = pandoc.Blocks {}, {}, 0
  local function flush()
    if #notes > 0 then blocks:extend(notes_section(ch, notes)) end
    notes = {}
  end
  local function fix_notes(b)
    if not endnotes then
      -- real footnotes (DOCX): the format numbers them, so drop the printed label
      return b:walk { Note = function(el) take_label(el.content); return el end }
    end
    return b:walk {
      traverse = 'topdown',
      Note = function(el)
        local n = #notes + 1
        local lab = take_label(el.content) or tostring(n)
        table.insert(notes, { content = el.content, label = lab })
        if not epub then return pandoc.Str('[' .. lab .. ']'), false end
        return html(string.format(
          '<a class="noteref" href="#c%d-n%d" id="c%d-r%d" epub:type="noteref" role="doc-noteref"><sup>%s</sup></a>',
          ch, n, ch, n, esc(lab))), false
      end,
    }
  end
  for _, b in ipairs(doc.blocks) do
    if b.t == 'Div' and b.classes:includes('part-end') then
      if endnotes then flush() end
      if #b.content > 0 then blocks:insert(pandoc.Div(b.content)) end
    elseif b.t == 'Header' and b.level == 1 then
      if endnotes then flush() end
      ch = ch + 1
      blocks:extend(labelled(fix_notes(b)))   -- a note on the title belongs to the new chapter
    else
      blocks:insert(fix_notes(b))
    end
  end
  if endnotes then flush() end
  doc.blocks = blocks
  return doc
end

function Pandoc(doc)
  local nt = doc.meta['notes-title']
  if nt then notes_title = pandoc.utils.stringify(nt) end
  doc = page_anchors(doc)
  doc = index_links(doc)
  return chapters(doc)
end
