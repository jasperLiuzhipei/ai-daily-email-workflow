"""Production newspaper layout: editable text, date-stable accents, no image API."""
from copy import deepcopy
from datetime import date
from html import escape
from pathlib import Path
import hashlib
import json
import re
from bs4 import BeautifulSoup
from email_feedback import render_feedback_block
from newspaper_sections import section_indices

ROOT = Path(__file__).resolve().parents[1]

def config():
    path = ROOT / 'config/newspaper_presentation.json'
    return json.loads(path.read_text()) if path.exists() else {}

def enabled(run_date):
    c = config()
    return c.get('mode') == 'newspaper_text' and str(run_date) >= c.get('effective_date', '9999-12-31')

def illustrated(run_date):
    c = config()
    return enabled(run_date) and str(run_date) >= c.get('illustrated_effective_date', '9999-12-31')

def text_only(run_date):
    return enabled(run_date) and not illustrated(run_date)

def configure_content(data, run_date):
    data = deepcopy(data)
    if not enabled(run_date):
        return data
    c = config()
    palette = json.loads((ROOT / 'config/newspaper_accent_palette.json').read_text())
    saved = data.get('presentation', {})
    if saved.get('version') == c['version'] and saved.get('date') == str(run_date):
        accent = saved['accent']
    else:
        accent = palette['colors'][(date.fromisoformat(str(run_date)) - date.fromisoformat(palette['base_date'])).days % len(palette['colors'])]
    data['presentation'] = {'mode': 'newspaper_text', 'version': c['version'], 'date': str(run_date), 'accent': accent, 'palette_version': palette['version']}
    if any(n.get('event_id') or n.get('display_tier') in {'brief', 'deep'} for n in data.get('news', [])):
        data['presentation']['order_policy'] = 'editor_order'
    if illustrated(run_date) and research(data):
        data['presentation']['mode'] = 'newspaper_illustrated'
    else:
        for paper in research(data):
            paper.pop('image', None)
            paper.pop('image_alt', None)
    return data

def research(data):
    items = data.get('research_items')
    if isinstance(items, list) and items:
        return items
    return [data['paper']] if isinstance(data.get('paper'), dict) else []

def e(text):
    return escape(str(text or ''), quote=True)

def a(url, label):
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(label)}</a>'

def p(text, cls=''):
    if not str(text or '').strip():
        return ''
    return f'<p class="{cls}">{e(text)}</p>'

def research_paragraphs(text):
    return ''.join(p(part.strip()) for part in re.split(r'\n\s*\n', str(text or '')) if part.strip())

def render_email(data, *, editorial_override=None):
    from premailer import transform
    s = BeautifulSoup((ROOT / 'templates/newspaper_text.html').read_text(), 'html.parser')
    def put(selector, html):
        n = s.select_one(selector)
        n.clear()
        n.append(BeautifulSoup(html, 'html.parser'))
    run_date = data['presentation']['date']
    accent = data['presentation']['accent']
    papers, news = research(data), data['news']
    s.title.string = f"AI 情报雷达｜{date.fromisoformat(run_date).isocalendar().year}年第{date.fromisoformat(run_date).isocalendar().week}周"
    s.select_one('#radar-newspaper')['data-accent'] = accent['id']
    s.select_one('.np-paper')['style'] = '--np-accent:' + accent['color'] + ';'
    put('.np-edition', f'VOLUME {int(data["issue"])}<time datetime="{run_date}">{run_date.replace("-", " / ")}</time>')
    s.select('.np-strap span')[1].string = f'{len(news)} 条动态 · {len(papers)} 篇研究'
    put('.np-headline', e(data.get('summary_headline') or '今日 AI 前沿与实践'))
    put('.np-deck', e(data['editor_note']))
    # Editors may save reviewed text sections beside an issue. Bind the override
    # to content bytes so later content changes cannot silently reuse old copy.
    override_path = Path('daily_runs') / run_date / 'newspaper_editorial.json'
    override = (json.loads(override_path.read_text()) if override_path.exists() else {}) if editorial_override is None else editorial_override
    signature = content_signature(data)
    if override and override.get('content_signature') != signature:
        raise ValueError('Reviewed newspaper sections do not match current content.')
    lead_i, supplement_i = section_indices(news)
    quick = [n for i,n in enumerate(news) if i not in (lead_i, supplement_i)]
    lead = news[lead_i] if lead_i is not None else None
    if lead:
        put('#np-lead', '<p class="np-kicker"><span class="np-kicker-en">01 / THE LEAD</span>重点解读</p>' + f'<h2 class="np-title">{a(lead["url"],lead["title"])}</h2>' + '<div class="np-lead-columns"><div>' + p(lead['one_liner']) + p(lead['why'],'np-check') + f'<p class="np-source">{a(lead["url"],"阅读原文 ↗")}</p></div><aside class="np-insight"><h3>部门能力启发</h3>' + p(lead.get('observation','')) + p(lead.get('boundary',''),'np-check') + '</aside></div>')
    else:
        s.select_one('#np-lead').decompose()
    blocks = []
    for paper in papers:
        evidence = paper['evidence']
        if isinstance(evidence,list):
            evidence = '；'.join(x['label']+'：'+x['text'] for x in evidence)
        blocks.append('<article class="rd-paper"><p class="rd-kicker"><span>02 / RESEARCH</span>研究解读</p>' + f'<h1>{e(paper["title"])}</h1>' + p(paper.get('conclusion') or paper.get('one_liner'),'rd-deck') + '<ol class="rd-steps">' + ''.join(f'<li class="rd-step"><span class="rd-num">{num}</span><div><h2>{label}</h2>{research_paragraphs(text)}</div></li>' for num,label,text in [('01','研究问题',paper.get('problem') or paper.get('question')),('02','方法机制',paper.get('mechanism') or paper.get('core_idea'))]) + '</ol><section class="rd-support"><h2>关键证据</h2>'+research_paragraphs(evidence)+'</section><aside class="rd-support"><h2>部门能力启发</h2>'+p(paper['insight'])+'</aside>'+p(paper['boundary'],'rd-boundary')+'<p class="rd-source">'+' · '.join(a(x['url'],x['label']+' ↗') for x in paper['links'])+'</p></article>')
    put('#np-research', '<div id="research-directions">'+''.join(blocks)+'</div>')
    if supplement_i is not None:
        n = news[supplement_i]
        put('#np-supplement','<p class="np-kicker"><span class="np-kicker-en">03 / SECOND LOOK</span>重要补充</p>'+f'<h2 class="np-title np-supplement-title">{a(n["url"],n["title"])}</h2>'+p(n['one_liner'])+p(n['why'],'np-one-insight')+p(n.get('observation',''),'np-one-insight')+p(n.get('boundary',''),'np-boundary')+f'<p class="np-source">{a(n["url"],"阅读原文 ↗")}</p>')
    put('#np-quick','<p class="np-kicker"><span class="np-kicker-en">04 / IN BRIEF</span>更多动态</p><div class="np-briefs">'+''.join('<article class="np-brief">'+f'<h3>{a(n["url"],n["title"]+" ↗")}</h3>'+p(n['one_liner'])+p(n['why'])+(p(n.get('observation','')) if n.get('display_tier') != 'brief' else '')+p(n.get('boundary',''),'np-check')+'</article>' for n in quick)+'</div>')
    toc = [('np-lead','重点解读','1 条',lead['title'])] if lead else []
    if papers: toc.append(('np-research','研究解读',f'{len(papers)} 篇',papers[0]['title']))
    else: s.select_one('#np-research').decompose()
    if supplement_i is not None:toc.append(('np-supplement','重要补充','1 条',news[supplement_i]['title']))
    else:s.select_one('#np-supplement').decompose()
    if quick:toc.append(('np-quick','更多动态',f'{len(quick)} 条','本期其余新闻与简讯'))
    else:s.select_one('#np-quick').decompose()
    put('.np-toc ol',''.join(f'<li><a href="#{id}"><strong>{label} <span class="np-count">{count} ↘</span></strong><span class="np-toc-summary">{e(title)}</span></a></li>' for id,label,count,title in toc))
    if override:
        allowed = {'.np-headline','.np-deck','.np-toc ol','#np-lead','#np-research','#np-supplement','#np-quick'}
        for selector,markup in override['sections'].items():
            if selector not in allowed:raise ValueError('Unsupported editorial section')
            frag=BeautifulSoup(markup,'html.parser')
            if frag.select('script,style,img,svg,canvas,iframe,object,embed'):raise ValueError('Editorial override must contain text markup only')
            put(selector,markup)
    if data['presentation']['mode'] == 'newspaper_illustrated':
        for article, paper in zip(s.select('#np-research .rd-paper'), papers, strict=True):
            image = paper.get('image')
            if not image: raise ValueError('Illustrated newspaper requires a reviewed research image.')
            caption = paper.get('image_caption', '')
            figure = '<figure class="research-figure" style="margin:24px 0 26px;background:#fff"><img src="'+e(image)+'" width="704" alt="'+e(paper.get('image_alt','研究图解'))+'" style="display:block;width:100%;max-width:100%;height:auto;border:0;background:#fff" />'+('<figcaption style="font-size:12px;line-height:1.8;color:#73716b;margin-top:10px">'+e(caption)+'</figcaption>' if caption else '')+'</figure>'
            article.select_one('.rd-deck').insert_after(BeautifulSoup(figure, 'html.parser'))
    # Tables and explicit inline styles preserve layout in email clients.
    def table(selector,widths):
        for node in list(s.select(selector)):
            children=list(node.find_all(recursive=False))
            t=s.new_tag('table',attrs={'role':'presentation','cellpadding':'0','cellspacing':'0','width':'100%','class':'mail-columns','style':'width:100%;border-collapse:collapse;table-layout:fixed;'})
            tr=s.new_tag('tr');t.append(tr)
            for i,child in enumerate(children):
                td=s.new_tag('td',attrs={'valign':'top','width':str(widths[min(i,len(widths)-1)])+'%','class':'mail-col','style':f'padding:0 {26 if i<len(children)-1 else 0}px 0 0;'})
                child.extract();td.append(child);tr.append(td)
            if selector == '.rd-step':
                cells=tr.find_all('td',recursive=False)
                cells[0]['width']='46'
                cells[0]['style']='width:46px;padding:0 12px 0 0;'
                cells[1].attrs.pop('width',None)
            node.append(t)
    table('.np-front',[70,30]);table('.np-name-row',[65,35]);table('.np-lead-columns',[57,43]);table('.rd-step',[8,92])
    # Briefs use pairs, with a full-width final item.
    for node in s.select('.np-briefs'):
        items=list(node.find_all('article',recursive=False));node.clear()
        for i in range(0,len(items),2):
            row=s.new_tag('div',attrs={'class':'brief-row'});node.append(row)
            for item in items[i:i+2]:row.append(item)
    table('.brief-row',[50,50])
    footer=s.select_one('.np-footer')
    feedback=BeautifulSoup('<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'+render_feedback_block()+'</table>','html.parser')
    feedback.select_one('[data-ai-daily-feedback] > td')['style']='padding:22px 0 0;'
    footer.insert_before(feedback)
    for script in s.select('script,link'):script.decompose()
    # Resolve CSS variables before inlining; email rendering must not require JS.
    variables={}
    allcss='\n'.join(x.get_text() for x in s.select('style'))
    for key,value in re.findall(r'(--[\w-]+)\s*:\s*([^;{}]+)',allcss):variables.setdefault(key,value.strip())
    variables.update({'--np-accent':accent['color'],'--rd-accent':accent['color']})
    text=str(s)
    for _ in range(8):
        text=re.sub(r'var\((--[\w-]+)\)',lambda m:variables.get(m[1],m[0]),text)
    # Drop container rules and replace them with supported viewport media rules.
    text=text.replace('@container newspaper (max-width: 490px)','@media (max-width: 600px)').replace('@container research-direction (max-width:440px)','@media (max-width:600px)')
    soup=BeautifulSoup(text,'html.parser')
    extra=soup.new_tag('style');extra.string='''
#radar-newspaper .np-front,#radar-newspaper .np-name-row,#radar-newspaper .np-lead-columns,#radar-newspaper .np-briefs,#research-directions .rd-step {display:block;}
#radar-newspaper .np-edition {text-align:right;}
#radar-newspaper .np-wordmark-ai {position:static;top:auto;vertical-align:-3px;}
#radar-newspaper .brief-row {margin-bottom:25px;}
#research-directions .rd-steps {margin-top:25px;}
#research-directions .rd-num {white-space:nowrap;}
@media(max-width:600px){.mail-columns .mail-col{display:block!important;width:100%!important;padding:0 0 20px!important;}#radar-newspaper .np-name-row .mail-col{display:table-cell!important;width:auto!important;padding:0!important;}#research-directions .rd-step .mail-col{display:table-cell!important;padding:0 10px 0 0!important;}#research-directions .rd-step .mail-col:first-child{width:42px!important;}.rd-step .mail-col:last-child{width:auto!important;}}
''';soup.select('style')[-1].insert_after(extra)
    ink_style=soup.new_tag('style');ink_style.string='#radar-newspaper .np-edition,#radar-newspaper .np-kicker-en,#radar-newspaper .np-count,#radar-newspaper .np-insight h3,#radar-newspaper .np-source,#research-directions .rd-kicker span,#research-directions .rd-support h2,#research-directions .rd-source a {color:'+accent.get('ink',accent['color'])+';}'
    extra.insert_after(ink_style)
    html=transform(str(soup),allow_network=False,disable_leftover_css=True,remove_classes=False,keep_style_tags=True,cssutils_logging_level=50)
    check=BeautifulSoup(html,'html.parser')
    if check.select('svg,canvas,script,iframe,object,embed'):raise ValueError('Newspaper must not include active content')
    if data['presentation']['mode'] == 'newspaper_text' and check.select('img'):raise ValueError('Text newspaper must not include images')
    check.body['data-ai-daily-presentation']=data['presentation']['mode']
    check.body['data-ai-daily-date']=run_date
    return str(check)

def content_signature(data):
    # Formatting metadata, image paths and generation provenance are excluded.
    clean={'news':data.get('news',[]),'paper':deepcopy(data.get('paper')),'research_items':deepcopy(data.get('research_items'))}
    for paper in research(clean):
        paper.pop('image',None);paper.pop('image_alt',None)
    return hashlib.sha256(json.dumps(clean,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def build_content_signature(data):
    # Keep the legacy override signature stable, but bind every visible shell
    # field as well as articles to the HTML built for production delivery.
    shell = {key: data.get(key) for key in (
        'date', 'display_date', 'issue', 'series', 'title', 'subtitle', 'eyebrow',
        'editor_note', 'summary_headline', 'summary', 'news_section', 'footer',
    )}
    shell['articles_sha256'] = content_signature(data)
    return hashlib.sha256(json.dumps(shell, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def assert_text_send(html_path, data, run_date):
    from ai_daily_common import file_sha256
    from validate_ai_daily_content import has_visual_relation_syntax
    html=Path(html_path).read_text()
    s=BeautifulSoup(html,'html.parser')
    marker=data.get('presentation',{})
    if marker.get('mode') not in ('newspaper_text','newspaper_illustrated') or marker.get('date')!=run_date:
        raise ValueError('Missing current text-presentation metadata; rebuild first.')
    if marker.get('html_sha256')!=file_sha256(Path(html_path)):
        raise ValueError('HTML changed since text build; rebuild first.')
    if marker.get('content_sha256') != build_content_signature(data):
        raise ValueError('Content changed or lacks build signature; rebuild first.')
    if not s.body or s.body.get('data-ai-daily-presentation')!=marker['mode'] or s.body.get('data-ai-daily-date')!=run_date:
        raise ValueError('Expected current newspaper HTML.')
    lead_i, supplement_i = section_indices(data.get('news', []))
    for selector, index in (('#np-lead', lead_i), ('#np-supplement', supplement_i)):
        section = s.select_one(selector)
        if index is None:
            if section:
                raise ValueError('Unexpected newspaper section; rebuild first.')
        else:
            title_link = section.select_one('.np-title a') if section else None
            if not title_link or title_link.get('href') != data['news'][index]['url']:
                raise ValueError('Newspaper feature section is missing or has the wrong topic; rebuild first.')
    expected_quick = [item['url'] for index, item in enumerate(data.get('news', []))
                      if index not in (lead_i, supplement_i)]
    actual_quick = [item.select_one('h3 a').get('href') if item.select_one('h3 a') else None
                    for item in s.select('.np-brief')]
    if actual_quick != expected_quick:
        raise ValueError('Newspaper section coverage differs from content; rebuild first.')
    if s.select('svg,canvas,script,iframe,object,embed'):
        raise ValueError('Newspaper contains active content.')
    research_section = s.select_one('#np-research')
    expected_papers = research(data)
    rendered_papers = research_section.select('.rd-paper') if research_section else []
    if len(rendered_papers) != len(expected_papers):
        raise ValueError('Newspaper research coverage differs from content; rebuild first.')
    for article, paper in zip(rendered_papers, expected_papers):
        if [link.get('href') for link in article.select('.rd-source a')] != [link['url'] for link in paper['links']]:
            raise ValueError('Newspaper research sources differ from content; rebuild first.')
    if research_section and has_visual_relation_syntax(research_section.get_text(' ', strip=True)):
        raise ValueError('Research HTML contains visual handoff relation syntax; rebuild the prose.')
    images=s.select('img')
    if marker['mode']=='newspaper_text':
        if images: raise ValueError('Text edition contains images.')
    else:
        expected=[(Path.cwd()/paper['image']).resolve() for paper in research(data)]
        actual=[(Path(html_path).resolve().parent/img.get('src','')).resolve() for img in images]
        if not expected or actual!=expected:
            raise ValueError('HTML images differ from reviewed content assets.')
