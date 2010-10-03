#!/usr/bin/python2.6

# Copyright 2010 Mark Longair

#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import os
import re
from datetime import date
from subprocess import check_call, call
from hashlib import sha1
from urllib2 import urlopen
from lxml import etree
import time
from StringIO import StringIO
import gd

# This script will create an opf version of The Guardian (or The
# Observer on Sunday) suitable for turning into a .mobi file for
# copying to your Kindle.

# The script has several dependencies:
#
#  1. sudo apt-get install python2.6-minimal python-gd python-lxml imagemagick ttf-mscorefonts-installer
#
# Also, if the kindlegen binary is on your PATH, a version of the book
# for kindle will be generated.  (Otherwise you just have the OPF
# version.)  kindlegen is available from:
#   http://www.amazon.com/gp/feature.html?ie=UTF8&docId=1000234621
#
# ========================================================================

# Things To Do:
#  - Generate XML / HTML with lxml (or some other less brittle way)
#  - Indicate missing pages in the contents and (?) by including pages
#    with no body

api_key = None

with open(os.path.join(os.environ['HOME'],
                       '.guardian-open-platform-key')) as fp:
    api_key = fp.read().strip()

def ordinal_suffix(n):
    if n == 1:
        return "st"
    elif n == 2:
        return "nd"
    else:
        return "th"

today_date = date.today()
today = str(today_date)
day = today_date.day
today_long = today_date.strftime("%A the {0}{1} of %B, %Y").format(day,ordinal_suffix(day))
check_call(['mkdir','-p',today])
os.chdir(today)

sunday = (date.today().isoweekday() == 7)

# Set up various variable that we need below:

paper = "The Observer" if sunday else "The Guardian"
book_id = "Guardian_"+today
book_title = paper + " on "+today_long
book_title_short = paper + " on " + today
book_basename = "guardian-"+today

# Now draw the cover image:

cover_image_basename = "cover-image"
cover_image_filename_png = cover_image_basename + ".png"
cover_image_filename = cover_image_basename + ".gif"

w = 600
h = 800

top_offset = 100

# font_filename = "/usr/share/fonts/truetype/ttf-dejavu/DejaVuSans.ttf"
font_filename = "/usr/share/fonts/truetype/msttcorefonts/arial.ttf"

new_cover_image = gd.image((w,h))
white = new_cover_image.colorAllocate((255,255,255))
black = new_cover_image.colorAllocate((0,0,0))

new_cover_image.fill((0,0),white)

logo_filename = os.path.join(
    "..",
    ("observer" if sunday else "guardian")+"-logo-500.png")

logo_image = gd.image(logo_filename)
logo_size = logo_image.size()

logo_image.copyTo( new_cover_image, ((600-logo_size[0])/2,top_offset) )

y = top_offset + logo_size[1] + top_offset

subtitle = today_long + "\n\nUnoffical Kindle version based on the Guardian Open Platform"
subtitle += "\nEmail: Mark Longair <mark-guardiankindle@longair.net>"

new_cover_image.string_ttf(
    font_filename,
    14,
    0,
    (28,y),
    subtitle,
    black)

with open(cover_image_filename_png,"w") as fp:
    new_cover_image.writePng(fp)

check_call(["convert",cover_image_filename_png,cover_image_filename])

def make_content_url(date, page ):
    return 'http://content.guardianapis.com/search?from-date={d}&to-date={d}&page={p}&page-size=20&order-by=newest&format=xml&show-fields=all&show-tags=all&show-factboxes=all&show-refinements=all&api-key={k}'.format( d=str(date), p=page, k=api_key)

def make_item_url(item_id):
    return 'http://content.guardianapis.com/{i}?format=xml&show-fields=all&show-editors-picks=true&show-most-viewed=true&api-key={k}'.format( i=item_id, k=api_key)

def url_to_element_tree(url):
    h = sha1(url.encode('UTF-8')).hexdigest()
    filename = h+".xml"
    if not os.path.exists(filename):
        try:
            text = urlopen(url).read()
        except:
            return None
        # Sleep to avoid making API requests faster than is allowed:
        time.sleep(0.6)
        with open(filename,"w") as fp:
            fp.write(text)
    return etree.parse(filename)

def url_to_root_element(url):
    return url_to_element_tree(url).getroot()

today_page_url = "http://www.guardian.co.uk/theguardian/all"
if sunday:
    today_page_url = "http://www.guardian.co.uk/theobserver/all"

today_page = urlopen(today_page_url).read()
today_filename = 'today.html'

with open(today_filename,"w") as fp:
    fp.write(today_page)

html_parser = etree.HTMLParser()

filename_to_headline = {}
filename_to_paper_part = {}

files = []

with open(today_filename) as fp:
    element_tree = etree.parse(today_filename,html_parser)
    page_number = 1
    for li in element_tree.find('//ul[@class="timeline"]'):
        paper_part = li.find('h2').find('a').text
        print "["+paper_part+"]"

        for li in li.find('ul'):
            link = li.find('a')
            href = link.get('href')
            m = re.search('http://www\.guardian\.co\.uk/(.*)$',href)
            item_id = m.group(1)
            print "  "+item_id
            item_url = make_item_url(item_id)
            element_tree = url_to_element_tree(item_url)
            if not element_tree:
                print "    Failed to fetch.  Skipping..."
                continue

            response_element = element_tree.getroot()
            if response_element.tag != 'response':
                print "The root tag unexpectedly wasn't \"response\"."
                sys.exit(1)
            status = response_element.attrib['status']
            if status != "ok":
                print "    The response element's status was \"{0}\" (i.e. not \"ok\") Skipping...".format(status)
                continue

            # FIXME: check the response element here for "ok"

            content = element_tree.find('//content')
            section_name = content.attrib['section-name']

            standfirst = None
            trail_text = None
            byline = None
            body = None
            headline = '[no headline found]'
            thumbnail = None
            short_url = None
            publication = None

            for field in element_tree.find('//fields'):
                name = field.get('name')
                if name == 'standfirst':
                    standfirst = field.text
                elif name == 'trail-text':
                    trail_text = field.text
                elif name == 'byline':
                    byline = field.text
                elif name == 'body':
                    body = field.text
                elif name == 'headline':
                    headline = field.text
                elif name == 'thumbnail':
                    thumbnail = field.text
                elif name == 'short-url':
                    short_url = field.text
                elif name == 'publication':
                    publication = field.text

            if body and re.search('Redistribution rights for this field are unavailable',body) and len(body) < 100:
                print "    Warning: redistribution rights unavailable - skipping..."
                continue

            page_filename = "{0:03d}.html".format(page_number)

            with open(page_filename,"w") as page_fp:
                page_fp.write('''<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN" "http://www.w3.org/TR/REC-html40/loose.dtd">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>{paper} on {today}: [{page}] {headline}</title>
</head>
<body>
'''.format(paper=paper,today=today,page=page_number,headline=headline.encode('UTF-8')))

                page_fp.write('<h1>{h}</h1>\n'.format(h=headline.encode('UTF-8')))
                if byline:
                    page_fp.write('<h4>By {b}</h4>'.format(b=byline.encode('UTF-8')))
                page_fp.write('<p>[{p}: {s}]</p>'.format(p=paper_part,s=section_name))
                if standfirst:
                    page_fp.write('<p><em>{sf}</em></p>'.format(sf=standfirst.encode('UTF-8')))

                if thumbnail:
                    extension = re.sub('^.*\.','',thumbnail)
                    thumbnail_filename = "{0:03d}-thumb.{1:}".format(page_number,extension)
                    if not os.path.exists(thumbnail_filename):
                        with open(thumbnail_filename,"w") as fp:
                            fp.write(urlopen(thumbnail).read())
                    files.append(thumbnail_filename)
                    page_fp.write('<img src="{iu}"></img>'.format(iu=thumbnail_filename))
                if body:
                    body_element_tree = etree.parse(StringIO(body),html_parser)
                    image_elements = body_element_tree.findall('//img')
                    for i, image_element in enumerate(image_elements):
                        ad_url = image_element.attrib['src']
                        ad_filename = '{0:03d}-ad-{1:02d}.gif'.format(page_number,i)
                        if not os.path.exists(ad_filename):
                            with open(ad_filename,'w') as fp:
                                fp.write(urlopen(ad_url).read())
                        image_element.attrib['src'] = ad_filename
                        files.append(ad_filename)
                    for e in body_element_tree.getroot()[0]:
                        page_fp.write(etree.tostring(e, pretty_print=True))
                if short_url:
                    page_fp.write('\n<p>Original story: <a href="{u}">{u}</a></p>\n'.format(u=short_url))
                    page_fp.write('<p>Content from the <a href="{u}">Guardian Open Platform</a></p>\n'.format(u="http://www.guardian.co.uk/open-platform"))
                page_fp.write('\n</body></html>')
            filename_to_headline[page_filename] = headline
            filename_to_paper_part[page_filename] = paper_part

            page_number += 1
            files.append(page_filename)

def extension_to_media_type(extension):
    if extension == 'gif':
        return 'image/gif'
    elif extension == 'html':
        return 'application/xhtml+xml'
    elif extension == 'jpg':
        return 'image/jpeg'
    elif extension == 'ncx':
        return 'application/x-dtbncx+xml'
    else:
        raise Exception, "Unknown extension: "+extension

# Now write the OPF:

contents_filename = "contents.html"
nav_contents_filename = "nav-contents.ncx"

spine = '    <itemref idref="contents"/>\n'

with open(contents_filename,"w") as fp:

    fp.write('''<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN" "http://www.w3.org/TR/REC-html40/loose.dtd">
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>Table of Contents</title>
</head>
<body>
<h1>Contents</h1>\n''')

    current_part = None

    for f in files:
        if re.search('\.html$',f):
            part_for_this_file = filename_to_paper_part[f]
            if current_part != part_for_this_file:
                if current_part:
                    fp.write('  </ul>\n')
                fp.write('<h4>{p}</h4>\n'.format(p=part_for_this_file))
                fp.write('  <ul>\n')
            current_part = part_for_this_file
            fp.write('    <li><a href="{f}">{h}</a></li>\n'.format(f=f,h=filename_to_headline[f].encode('UTF-8')))
            spine += '    <itemref idref="{item_id}"/>\n'.format(item_id=re.sub('\..*$','',f))

    fp.write('''
  </ul>
</body>
</html>''')

filename_to_headline[contents_filename] = "Table of Contents"

with open(nav_contents_filename,"w") as fp:
    fp.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"
        "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">

<!--
        For a detailed description of NCX usage please refer to:
        http://www.idpf.org/2007/opf/OPF_2.0_final_spec.html#Section2.4.1
-->

<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="en-US">
<head>
<meta name="dtb:uid" content="{book_id}"/>
<meta name="dtb:depth" content="2"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{title}</text></docTitle>
<docAuthor><text>{author}</text></docAuthor>
  <navMap>
'''.format(book_id=book_id,title=book_title_short,author=paper))
    nav_contents_files = [ contents_filename ] + [ x for x in files if re.search('\.html$',x) ]
    i = 1
    for f in nav_contents_files:
        point_class = "chapter"
        item_id = re.sub('\..*$','',f)
        if f == contents_filename:
            point_class = "toc"
        fp.write('<navPoint class="{point_class}" id="{item_id}" playOrder="{play_index}"><navLabel><text>{title}</text></navLabel><content src="{f}"/></navPoint>\n'.format(
                point_class = point_class,
                item_id = item_id,
                play_index = i,
                title = filename_to_headline[f].encode('UTF-8'),
                f = f))
        i += 1
    fp.write('</navMap></ncx>')

files.append(contents_filename)
files.append(nav_contents_filename)

files.append(cover_image_filename)

manifest = ""

for f in files:
    item_id = re.sub('\..*$','',f)
    extension = re.sub('^.*\.','',f)
    manifest += '    <item id="{item_id}" media-type="{media_type}" href="{filename}"/>\n'.format(
        item_id=item_id,
        media_type=extension_to_media_type(extension),
        filename=f)

opf_filename = book_basename+".opf"
mobi_filename = book_basename+".mobi"

with open(opf_filename,"w") as fp:
    fp.write('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="{book_id}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{book_title}</dc:title>
    <dc:language>en-gb</dc:language>
    <meta name="cover" content="{cover_id}"/>
    <dc:creator>{creator}</dc:creator>
    <dc:publisher>{publisher}</dc:publisher>
    <dc:subject>News</dc:subject>
    <dc:date>{publication_date}</dc:date>
    <dc:description>{description}</dc:description>
  </metadata>

  <manifest>
{all_files}
  </manifest>

  <spine toc="nav-contents">
{spine}
  </spine>

  <guide>
    <reference type="toc" title="Table of Contents" href="{contents_filename}"></reference>
    <reference type="text" title="{first_page_title}" href="{first_page_filename}"></reference>
  </guide>

</package>
'''.format( book_id = book_id,
            book_title = book_title_short,
            cover_id = "cover-image",
            creator = paper,
            publisher = paper,
            publication_date = today,
            description = "An unofficial ebook edition of {0} on {1}".format(paper,today_long),
            all_files = manifest,
            spine = spine,
            contents_filename = contents_filename,
            first_page_filename = '001.html',
            first_page_title = filename_to_headline['001.html']
            ))

with open("/dev/null","w") as null:
    if 0 == call(['kindlegen'],stdout=null):
        # The kindlegen is available:
        call(['kindlegen','-o',mobi_filename,opf_filename])
    else:
        print "Warning: kindlegen was not on your path; not generating .mobi version"
