from __future__ import absolute_import
from __future__ import unicode_literals
import markdown
from markdown import Extension
from markdown.blockprocessors import BlockProcessor
from markdown.preprocessors import Preprocessor
from markdown.util import AtomicString
import markdown.extensions.codehilite
import re
import base64
import mimetypes

from ..converter import KnowledgePostConverter
from ..mapping import SubstitutionMapper

MARKDOWN_EXTENSTIONS = ['markdown.extensions.extra',
                        'markdown.extensions.abbr',
                        'markdown.extensions.attr_list',
                        'markdown.extensions.def_list',
                        'markdown.extensions.fenced_code',
                        'markdown.extensions.footnotes',
                        'markdown.extensions.tables',
                        'markdown.extensions.smart_strong',
                        'markdown.extensions.admonition',
                        markdown.extensions.codehilite.CodeHiliteExtension(guess_lang=False),
                        'markdown.extensions.headerid',
                        'markdown.extensions.meta',
                        'markdown.extensions.sane_lists',
                        'markdown.extensions.smarty',
                        'markdown.extensions.toc(baselevel=1)',
                        'markdown.extensions.wikilinks',
                        'knowledge_repo.converters.html:KnowledgeMetaExtension',
                        'knowledge_repo.converters.html:MathJaxExtension',
                        'knowledge_repo.converters.html:IndentsAsCellOutput']


class IndentsAsCellOutputProcessor(BlockProcessor):
    """ Process code blocks. """

    def test(self, parent, block):
        return block.startswith(' ' * self.tab_length)

    def run(self, parent, blocks):
        sibling = self.lastChild(parent)
        block = blocks.pop(0)

        block, theRest = self.detab(block)
        block = block.rstrip()

        block_is_html = False
        if "<div " in block or "</" in block or "<span " in block:
            block_is_html = True

        if (sibling is not None and sibling.tag == "div"):
            # The previous block was a code block. As blank lines do not start
            # new code blocks, append this block to the previous, adding back
            # linebreaks removed from the split into a list.

            block_is_html = block_is_html and not isinstance(sibling.text, AtomicString)

            block = '\n'.join([sibling.text, block])
            output = sibling
        else:
            # This is a new codeblock. Create the elements and insert text.
            output = markdown.util.etree.SubElement(parent, 'div', {'class': 'code-output'})

        # If not HTML, add the `pre` class so that we know to render output as raw text
        if not block_is_html and 'pre' not in output.get('class', 'code-output'):
            output.set('class', ' '.join([output.get('class', ''), 'pre']))

        output.text = "{}\n".format(block) if block_is_html else AtomicString("{}\n".format(block))

        if theRest:
            # This block contained unindented line(s) after the first indented
            # line. Insert these lines as the first block of the master blocks
            # list for future processing.
            blocks.insert(0, theRest)


class IndentsAsCellOutput(Extension):

    def extendMarkdown(self, md, md_globals):
        md.parser.blockprocessors['code'] = IndentsAsCellOutputProcessor(md.parser)


class KnowledgeMetaPreprocessor(Preprocessor):
    """ Get Meta-Data. """

    def run(self, lines):
        """ Parse Meta-Data and store in Markdown.Meta. """
        cnt = 0
        for i, line in enumerate(lines):
            if line.strip() == '---':
                cnt = cnt + 1
            if cnt == 2:
                break
        return lines[i + 1:]


class KnowledgeMetaExtension(Extension):
    """ Meta-Data extension for Python-Markdown. """

    def extendMarkdown(self, md, md_globals):
        """ Add MetaPreprocessor to Markdown instance. """
        md.preprocessors.add("knowledge_meta",
                             KnowledgeMetaPreprocessor(md),
                             ">normalize_whitespace")


class MathJaxPattern(markdown.inlinepatterns.Pattern):

    def __init__(self):
        markdown.inlinepatterns.Pattern.__init__(self, r'(?<!\\)(\$\$?)(.+?)\2')

    def handleMatch(self, m):
        node = markdown.util.etree.Element('mathjax')
        node.text = markdown.util.AtomicString(m.group(2) + m.group(3) + m.group(2))
        return node


class MathJaxExtension(markdown.Extension):
    def extendMarkdown(self, md, md_globals):
        # Needs to come before escape matching because \ is pretty important in LaTeX
        md.inlinePatterns.add('mathjax', MathJaxPattern(), '<escape')


class HTMLConverter(KnowledgePostConverter):
    '''
    Use this as a template for new KnowledgePostConverters.
    '''
    _registry_keys = ['html']

    def init(self):
        self.kp_images = self.kp.read_images()

    def to_string(self, skip_headers=False, images_base64_encode=True, urlmappers=[]):
        """
        Renders the markdown as html
        """
        # Copy urlmappers locally so we can modify it without affecting global
        # state
        urlmappers = urlmappers[:]
        if images_base64_encode:
            urlmappers.insert(0, self.base64_encode_image_mapper)

        # proxy posts are assumed to be embeddable links
        if 'proxy' in self.kp.headers:
            return '<a href="{0}">Linked Post</a>\n<iframe width=100% height=800 src="{0}"></iframe>'.format(self.kp.headers['proxy'].strip())

        html = ''
        if not skip_headers:
            html += self.render_headers()

        html += markdown.Markdown(extensions=MARKDOWN_EXTENSTIONS).convert(self.kp.read())

        return self.apply_url_remapping(html, urlmappers)

    def apply_url_remapping(self, html, urlmappers):
        patterns = {
            'img': '<img.*?src=[\'"](?P<url>.*?)[\'"].*?>',
            'a': '<a.*?href=[\'"](?P<url>.*?)[\'"].*?>'
        }

        def urlmapper_proxy(name, match):
            for urlmapper in urlmappers:
                new_url = urlmapper(name, match.group('url'))
                if new_url is not None:
                    break
            if new_url is not None:
                return re.sub('(src|href)=[\'"](?:.*?)[\'"]', '\\1="{}"'.format(new_url), match.group(0))
            return None

        return SubstitutionMapper(patterns=patterns, mappers=[urlmapper_proxy]).apply(html)

    # Utility methods
    def render_headers(self):
        headers = self.kp.headers

        headers['authors_string'] = ', '.join(headers.get('authors'))
        headers['tldr'] = markdown.Markdown(extensions=MARKDOWN_EXTENSTIONS[
                                            :-1]).convert(headers['tldr'])
        headers['date_created'] = headers['created_at'].isoformat()
        headers['date_updated'] = headers['updated_at'].isoformat()

        header = """
<h1>{title}</h1>
<p id='metadata'>
<strong>Author</strong>: {authors_string} <br>
<strong>Date Created</strong>: {date_created}<br>
<strong>Date Updated</strong>: {date_updated}<br>
<strong>Tags</strong><text>: </text><br>
<strong>TLDR</strong>: {tldr}<br>
</p>
""".format(**headers)

        return header

    def base64_encode_image_mapper(self, tag, url):
        if tag == 'img':
            if url in self.kp_images:
                image_data = base64.b64encode(self.kp_images[url])
                image_mimetype = mimetypes.guess_type(url)[0]
                if image_mimetype is not None:
                    return 'data:{};base64, '.format(image_mimetype) + image_data.decode('utf-8')
        return None
