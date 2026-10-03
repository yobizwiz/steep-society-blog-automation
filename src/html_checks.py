"""Semantic HTML checks, independent of presentation colors."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


class Node:
    def __init__(self, tag='', attrs=(), parent=None, start=0):
        self.tag, self.attrs, self.parent, self.start = tag, dict(attrs), parent, start
        self.end, self.children, self.text, self.parts = start, [], '', []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def visible_text(self):
        if self.tag in ('script', 'style'): return ''
        text = ''.join(p.visible_text() if isinstance(p, Node) else p for p in self.parts)
        return text + ' ' if self.tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'td', 'th', 'section') else text


class Document(HTMLParser):
    VOID = {'img', 'br', 'hr', 'meta', 'link', 'input', 'source', 'wbr', 'area', 'base', 'col', 'embed', 'param', 'track'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html, self.lines, self.root = html, [0], Node()
        self.lines.extend(m.end() for m in re.finditer('\n', html))
        self.stack = [self.root]
        self.feed(html)
        self.close()
        for node in self.stack: node.end = len(html)

    def char_offset(self):
        row, col = self.getpos()
        return self.lines[row - 1] + col

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1], self.char_offset())
        node.end = node.start + len(self.get_starttag_text())
        self.stack[-1].children.append(node)
        self.stack[-1].parts.append(node)
        if tag not in self.VOID: self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID: self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                for node in self.stack[i:]: node.end = self.char_offset() + len(tag) + 3
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].text += data
        self.stack[-1].parts.append(data)

    def nodes(self, tag=None):
        return [n for n in self.root.walk() if tag is None or n.tag == tag]

    def ctas(self):
        explicit = [n for n in self.nodes() if n.attrs.get('data-cta') == 'primary']
        legacy = []
        for a in self.nodes('a'):
            path = urlsplit(a.attrs.get('href', '')).path
            style = re.sub(r'\s+', '', a.attrs.get('style', '').lower())
            if not re.match(r'^/(products|collections)/[^/]+/?$', path): continue
            button_class = any(re.search(r'(?:^|[-_])(?:btn|button)(?:$|[-_])', token, re.I)
                               for token in a.attrs.get('class', '').split())
            if not (a.attrs.get('role') == 'button' or button_class
                    or ('display:inline-block' in style and 'background' in style)): continue
            parents, node = [], a
            while node.parent:
                parents.append(node)
                node = node.parent
            if any(n in explicit for n in parents): continue
            box = next((n for n in parents if n.tag in ('div', 'section', 'aside')), a)
            if box not in legacy: legacy.append(box)
        return explicit + legacy


def cta_issues(html, expected_url=None):
    doc = Document(html)
    boxes, issues = doc.ctas(), []
    if len(boxes) != 1:
        return [{'rule': 'cta_count', 'detail': f'Expected one semantic CTA; found {len(boxes)}'}]
    box = boxes[0]
    links = [n.attrs.get('href') for n in box.walk() if n.tag == 'a']
    if len(links) != 1 or not links[0] or (expected_url and links[0] != expected_url):
        issues.append({'rule': 'cta_destination', 'detail': 'Closing CTA must contain exactly one link to the scheduled destination'})
    headings = [n for n in doc.nodes() if n.tag in ('h2', 'h3', 'h4') and n.start < box.start]
    if not headings or ' '.join(headings[-1].visible_text().split()).casefold() != 'quick recap':
        issues.append({'rule': 'quick_recap', 'detail': 'Last heading before CTA must be Quick Recap'})
    tail = Document(html[box.end:])
    if tail.root.visible_text().strip() or tail.nodes('img'):
        issues.append({'rule': 'no_content_below_cta', 'detail': 'Visible content follows CTA'})
    return issues
