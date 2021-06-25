#!/usr/bin/env python3

# Copyright 2010, 2011 Mark Longair
# Copyright 2011 Dominic Evans
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as
#   published by the Free Software Foundation, either version 3 of the
#   License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import sys
import os
import re
from datetime import date
from subprocess import Popen, check_call, call, PIPE
from hashlib import sha1
from urllib.request import urlopen, HTTPError
import lxml
from lxml import etree
import time
from io import StringIO
import errno
from lxml.builder import E
from lxml.html import fragments_fromstring
from PIL import Image, ImageDraw, ImageFont

# This script will create an opf version of The Guardian (or The
# Observer on Sunday) suitable for turning into a .mobi file for
# copying to your Kindle.

# The script has several dependencies specified in requirements.txt - you
# can install them with: pip install -r requirements.txt
#
# You need to put your API key in ~/.guardian-open-platform-key
#
# Also, if the kindlegen binary is on your PATH, a version of the book
# for kindle will be generated.  (Otherwise you just have the OPF
# version.)  kindlegen is available from:
#   http://www.amazon.com/gp/feature.html?ie=UTF8&docId=1000234621
#
# ========================================================================

sleep_seconds_after_api_call = 2

api_key = None

with open(os.path.join(os.environ['HOME'],
                       '.guardian-open-platform-key')) as fp:
    api_key = fp.readlines()[0].strip()

def ordinal_suffix(n):
    if n == 1:
        return "st"
    elif n == 2:
        return "nd"
    elif n == 3:
        return "rd"
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
book_title_short = paper + " (Unofficial)"
book_basename = "guardian-"+today

# ========================================================================
# Now draw the cover image:

cover_image_basename = "cover-image"
cover_image_filename_png = cover_image_basename + ".png"
cover_image_filename = cover_image_basename + ".gif"
masthead_filename = "masthead.gif"

w = 600
h = 800

top_offset = 100

def backticks(command):
    p = Popen(command,stdout=PIPE)
    c = p.communicate()
    if p.returncode != 0:
        return None
    else:
        return c[0]

font_filename = backticks(['fc-match','-f','%{file}','Helvetica'])
if not font_filename:
    print("Failed to find a font matching Helvetica")
    sys.exit(1)

# Use the Python Imaging Library (PIL) to draw a simple cover image:

im = Image.new("L",(w,h),"white")

logo_filename = os.path.join(
    "..",
    ("observer" if sunday else "guardian")+"-logo-500.png")

im_logo = Image.open(logo_filename)
logo_size = im_logo.size

im.paste(im_logo,(int((w-logo_size[0])/2),top_offset))

subtitle = [ today_long,
             '',
             'Unoffical Kindle version based on the Guardian Open Platform',
             'Email: Mark Longair <mark-guardiankindle@longair.net>' ]

font = ImageFont.truetype(font_filename,18)
draw = ImageDraw.Draw(im)

sizes = [draw.textsize(line,font=font) for line in subtitle]
m_w = max(s[0] for s in sizes)
m_h = max(s[1] for s in sizes)

y = top_offset + logo_size[1] + top_offset
x = (w - m_w) / 2

for line in subtitle:
    draw.text((x,y),line,font=font,fill="black")
    y += m_h

im.save(cover_image_filename)

# Convert the logo to GIF to use as the masthead:
im = Image.open(logo_filename)
im.save(masthead_filename)

class ArticleAccessDenied(Exception):
    pass

class ArticleMissing(Exception):
    pass

def make_item_url(item_id):
    return 'http://content.guardianapis.com/{i}?format=xml&show-fields=all&show-editors-picks=true&show-most-viewed=true&api-key={k}'.format( i=item_id, k=api_key)

def get_error_message_from_content(http_error):
    try:
        error_data = json.load(http_error.fp)
        return error_data.get('response', {}).get('message', '')
    except ValueError:
        # In which case it's probably just not JSON in the response:
        return http_error.fp.read()

def url_to_element_tree(url):
    h = sha1(url.encode('UTF-8')).hexdigest()
    filename = h+".xml"
    if not os.path.exists(filename):
        try:
            text = urlopen(url).read().decode("utf-8")
        except HTTPError as e:
            print("e is:", e)
            if e.code == 403:
                time.sleep(sleep_seconds_after_api_call)
                error_message = get_error_message_from_content(e)
                raise ArticleAccessDenied(error_message)
            # Otherwise it's probably a 404, an article that's now been removed:
            elif e.code == 404:
                time.sleep(sleep_seconds_after_api_call)
                raise ArticleMissing(get_error_message_from_content(e))
            else:
                raise Exception("An unexpected HTTPError was returned: "+str(e))
        # Sleep to avoid making API requests faster than is allowed:
        time.sleep(sleep_seconds_after_api_call)
        with open(filename,"w") as fp:
            fp.write(text)
    return etree.parse(filename)

# ========================================================================
# Iterate over every link found in the "All Guardian Stories" page for
# today, and generate a version of each story in very simple HTML:

today_page_url = "http://www.theguardian.com/theguardian"
if sunday:
    today_page_url = "http://www.theguardian.com/theobserver"

today_filename = 'today.html'

if not os.path.exists(today_filename):
    today_page = urlopen(today_page_url).read().decode("utf-8")
    with open(today_filename,"w") as fp:
        fp.write(today_page)

html_parser = etree.HTMLParser()

filename_to_headline = {}
filename_to_description = {}
filename_to_author = {}
filename_to_section = {}

files = []

def strip_html(s):
    if s:
        return str(lxml.html.fromstring(s).text_content())
    else:
        return ""

def element_to_string(element):
    s = element.text or ""
    for sub_element in element:
        s += element_to_string(sub_element)
    s += element.tail
    return s

def get_sections_and_links(element_tree):
    result = []
    for section_div in element_tree.findall('//div[@class="fc-container__inner"]'):
        section_title_div = section_div.find('.//div[@class="fc-container__header__title"]')
        section = element_to_string(section_title_div).strip()
        link_xpath = './/a[@class="u-faux-block-link__overlay js-headline-text"]'
        result.append(
            (
                section,
                [
                    (a.attrib['href'], a.text)
                    for a in section_div.findall(link_xpath)
                ]
            )
        )
    return result

with open(today_filename) as fp:
    element_tree = etree.parse(today_filename,html_parser)
    page_number = 1
    for section, links in get_sections_and_links(element_tree):
        print(section)
        for link_url, link_text in links:
            headline = '[No headline found]'
            standfirst = None
            trail_text = None
            byline = None
            body = None
            thumbnail = None
            short_url = None
            publication = None
            section_name = None

            m = re.search('https?://www\.theguardian\.com/(.*)$',link_url)
            if not m:
                print(u"  Warning: failed to parse the link: '{0}'".format(link_url))
                continue
            item_id = m.group(1)
            print("  "+item_id)
            item_url = make_item_url(item_id)
            try:
                element_tree = url_to_element_tree(item_url)

                response_element = element_tree.getroot()
                if response_element.tag != 'response':
                    print("The root tag unexpectedly wasn't \"response\".")
                    sys.exit(1)
                status = response_element.attrib['status']
                if status != "ok":
                    print("    The response element's status was \"{0}\" (i.e. not \"ok\") Skipping...".format(status))
                    continue

                content = element_tree.find('//content')
                section_name = content.attrib['section-name']

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
                    print("    Warning: no redistribution rights available for that article")
                    body = "<p><b>Redistribution rights for this article were not available.</b></p>"

            except (ArticleMissing, ArticleAccessDenied) as e:
                print("    Warning: couldn't fetch that article")
                headline = link_text
                body = "<p><b>The Guardian Open Platform returned an error for that article: {0}</b></p>".format(e)
                body += '<p>You can still try <a href="{0}">the original article link</a></p>'.format(link_url)

            page_filename = "{0:03d}.html".format(page_number)

            html_body = E.body(E.h3(headline))
            if byline:
                html_body.append( E.h4('By '+byline) )
            html_body.append( E.p(u'[{s}]'.format(s=section)) )
            if standfirst:
                standfirst_fragments = fragments_fromstring(standfirst)
                standfirst_element = E.p( E.em( *standfirst_fragments ) )
                html_body.append( standfirst_element )
            if thumbnail:
                extension = re.sub('^.*\.','',thumbnail)
                thumbnail_filename = "{0:03d}-thumb.{1:}".format(page_number,extension)
                if not os.path.exists(thumbnail_filename):
                    with open(thumbnail_filename,"wb") as fp:
                        fp.write(urlopen(thumbnail).read())
                files.append(thumbnail_filename)
                html_body.append( E.p( E.img( { 'src': thumbnail_filename } ) ) )
            if body:
                body_element_tree = etree.parse(StringIO(body),html_parser)
                image_elements = body_element_tree.findall('//img')
                for i, image_element in enumerate(image_elements):
                    ad_url = image_element.attrib['src']
                    ad_image_data = urlopen(ad_url).read()
                    ad_image_hash = sha1(ad_image_data).hexdigest()
                    ad_filename = 'ad-{0}.gif'.format(ad_image_hash)
                    if not os.path.exists(ad_filename):
                        with open(ad_filename,'wb') as fp:
                            fp.write(ad_image_data)
                        files.append(ad_filename)
                    image_element.attrib['src'] = ad_filename
                for e in body_element_tree.getroot()[0]:
                    html_body.append(e)
            if short_url:
                html_body.append( E.p('Original story: ', E.a( { 'href': short_url }, short_url ) ) )
            html_body.append( E.p( 'Content from the ', E.a( { 'href' : 'http://www.guardian.co.uk/open-platform' }, "Guardian Open Platform" ) ) )

            html = E.html({ "xmlns": 'http://www.w3.org/1999/xhtml', "{http://www.w3.org/XML/1998/namespace}lang" : 'en', "lang": 'en' },
                    E.head( E.meta( { 'http-equiv' : 'Content-Type', 'content' : 'http://www.w3.org/1999/xhtml; charset=utf-8' } ),
                        E.title( u'{g} on {t}: [{p}] {h}'.format( g=paper, t=today, p=page_number, h=headline ) ),
                        E.meta( { 'name': 'author', 'content' : byline if byline else ''} ),
                        E.meta( { 'name': 'description', 'content' : standfirst if standfirst else ''} ) ),
                    html_body )

            with open(page_filename,"wb") as page_fp:
                page_fp.write( etree.tostring(html,pretty_print=True) )

            filename_to_headline[page_filename] = strip_html(headline)
            filename_to_section[page_filename] = section
            filename_to_description[page_filename] = strip_html(standfirst)
            filename_to_author[page_filename] = strip_html(byline)

            page_number += 1
            files.append(page_filename)

# ========================================================================
# Create the two contents files, one HTML and one NCX:

def extension_to_media_type(extension):
    if extension == 'gif':
        return 'image/gif'
    elif extension == 'html':
        return 'application/xhtml+xml'
    elif extension == 'jpg' or extension == 'jpeg':
        return 'image/jpeg'
    elif extension == 'png':
        return 'image/png'
    elif extension == 'ncx':
        return 'application/x-dtbncx+xml'
    else:
        raise Exception("Unknown extension: "+extension)

contents_filename = "contents.html"
nav_contents_filename = "nav-contents.ncx"

# (Build up the spine elements for the OPF in the same loop...)

spine = etree.Element("spine",
                      attrib={"toc" : "nav-contents"})

etree.SubElement(spine,"itemref",
                 attrib={"idref":"contents"})

body_element = E.body(E.h1("Contents"))

current_section = None
current_list = None

for f in files:
    if re.search('\.html$',f):
        section_for_this_file = filename_to_section[f]
        if current_section != section_for_this_file:
            body_element.append( E.h4( section_for_this_file ) )
            current_list = E.ul( )
            body_element.append( current_list )
        current_section = section_for_this_file
        current_list.append( E.li( E.a( { 'href': f }, filename_to_headline[f] ) ) )

        etree.SubElement(spine,"itemref",
                         attrib={"idref":re.sub('\..*$','',f)})

with open(contents_filename,"wb") as fp:
    html = E.html( E.head(
            E.meta( { 'http-equiv' : 'Content-Type',
                      'content' : 'text/html; charset=utf-8' } ),
            E.title( "Table of Contents" ) ),
                   body_element )
    fp.write(etree.tostring(html,pretty_print=True))

filename_to_headline[contents_filename] = "Table of Contents"

# ========================================================================
# Now generate the NCX table of contents:

mbp_namespace = "http://mobipocket.com/ns/mbp"
mbp = "{{{0}}}".format(mbp_namespace)

ncx_namespace = "http://www.daisy.org/z3986/2005/ncx/"
ncx_nsmap = { None: ncx_namespace, "mbp": mbp_namespace }

ncx = etree.Element("ncx",
                    nsmap=ncx_nsmap,
                    attrib={"version" : "2005-1",
                            "{http://www.w3.org/XML/1998/namespace}lang" : "en-GB"})

head = etree.SubElement(ncx,"head")
etree.SubElement(head,"meta",
                 attrib={"name" : "dtb:uid",
                         "content" : book_id })
etree.SubElement(head,"meta",
                 attrib={"name" : "dtb:depth",
                         "content" : "2" })
etree.SubElement(head,"meta",
                 attrib={"name" : "dtb:totalPageCount",
                         "content" : "0" })
etree.SubElement(head,"meta",
                 attrib={"name" : "dtb:maxPageNumber",
                         "content" : "0" })

title_text_element = etree.Element("text")
title_text_element.text = book_title_short
author_text_element = etree.Element("text")
author_text_element.text = paper

etree.SubElement(ncx,"docTitle").append(title_text_element)
etree.SubElement(ncx,"docAuthor").append(author_text_element)

nav_map = etree.SubElement(ncx,"navMap")

nav_point_periodical = etree.SubElement(nav_map, "navPoint", attrib={"class": "periodical", "id": 'periodical', "playOrder": '0'})
masthead = etree.Element(mbp+"meta-img",attrib={"name": "mastheadImage", "src": masthead_filename})
nav_point_periodical.append(masthead)
content = etree.Element("content",attrib={"src" : contents_filename})
title_text_element = etree.Element("text")
title_text_element.text = filename_to_headline[contents_filename]
nav_label = etree.SubElement(nav_point_periodical, "navLabel")
nav_label.append(title_text_element)
nav_point_periodical.append(content)

nav_contents_files = [ fn for fn in files if re.search('\.html$',fn) ]
i = 1
nav_point_section = nav_point_periodical
for f in nav_contents_files:
    section_for_this_file = filename_to_section[f]
    if current_section != section_for_this_file:
        nav_point_section = etree.SubElement(nav_point_periodical,"navPoint",
                                     attrib={"class" : "section",
                                             "id" : re.sub(' ','-',section_for_this_file),
                                             "playOrder" : str(i) })
        content = etree.Element("content",attrib={"src" : f})
        title_text_element = etree.Element("text")
        title_text_element.text = section_for_this_file
        nav_label = etree.SubElement(nav_point_section ,"navLabel")
        nav_label.append(title_text_element)
        nav_point_section.append(content)
        i += 1

    current_section = section_for_this_file
    item_id = re.sub('\..*$','',f)
    nav_point_article = etree.SubElement(nav_point_section,"navPoint",
                                 attrib={"class" : "article",
                                         "id" : 'item-'+item_id,
                                         "playOrder" : str(i) })
    content = etree.Element("content",attrib={"src" : f})
    title_text_element = etree.Element("text")
    title_text_element.text = filename_to_headline[f]
    nav_label = etree.SubElement(nav_point_article,"navLabel")
    nav_label.append(title_text_element)
    nav_point_article.append(content)

    if filename_to_description[f]:
        meta = etree.SubElement(nav_point_article, mbp+"meta", attrib={"name" : 'description'})
        meta.text = filename_to_description[f]

    if filename_to_author[f]:
        meta = etree.SubElement(nav_point_article, mbp+"meta", attrib={"name" : 'author'})
        meta.text = filename_to_author[f]

    i += 1

with open(nav_contents_filename,"wb") as fp:
    fp.write(b"<?xml version='1.0' encoding='utf-8'?>\n")
    # I don't think there's an elegant way of setting the
    # doctype using lxml.etree, but I could be wrong...
    fp.write(b'<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">\n')
    fp.write(etree.tostring(ncx,
                            pretty_print=True,
                            encoding="utf-8",
                            xml_declaration=False))

# ========================================================================

files.append(contents_filename)
files.append(nav_contents_filename)
files.append(cover_image_filename)
files.append(masthead_filename)

opf_filename = book_basename+".opf"
mobi_filename = book_basename+".mobi"

# ========================================================================
# Now generate the structure of the OPF file using lxml.etree:
opf_namespace = "http://www.idpf.org/2007/opf"
dc_namespace = "http://purl.org/dc/elements/1.1/"
dc_metadata_nsmap = { "dc" : dc_namespace }
dc = "{{{0}}}".format(dc_namespace)

package = etree.Element("{{{0}}}package".format(opf_namespace),
                        nsmap={None:opf_namespace},
                        attrib={"version":"2.0",
                                "unique-identifier":"Guardian_2010-10-15"})
metadata = etree.Element("metadata")
package.append( metadata )

dc_metadata = etree.Element("dc-metadata",
                         nsmap=dc_metadata_nsmap)
metadata.append( dc_metadata )
etree.SubElement(dc_metadata,dc+"title").text = book_title_short
etree.SubElement(dc_metadata,dc+"language").text = "en-gb"
etree.SubElement(dc_metadata,"meta",attrib={"name":"cover",
                                         "content":"cover-image"})
etree.SubElement(dc_metadata,dc+"creator").text = paper
etree.SubElement(dc_metadata,dc+"publisher").text = paper
etree.SubElement(dc_metadata,dc+"subject").text = "News"
etree.SubElement(dc_metadata,dc+"date").text = today
etree.SubElement(dc_metadata,dc+"description").text = "An unofficial ebook edition of {0} on {1}".format(paper,today_long)

x_metadata = etree.Element("x-metadata")
metadata.append( x_metadata )
etree.SubElement(x_metadata,"output",attrib={"encoding":"utf-8",
                                         "content-type":"application/x-mobipocket-subscription-magazine"})

manifest = etree.SubElement(package,"manifest")

for f in files:
    item_id = re.sub('\..*$','',f)
    extension = re.sub('^.*\.','',f)
    etree.SubElement(manifest,"item",
                     attrib={"id" : item_id,
                             "media-type" : extension_to_media_type(extension),
                             "href" : f})

package.append(spine)

guide = etree.SubElement(package,"guide")
etree.SubElement(guide,"reference",
                 attrib={"type":"toc",
                         "title":"Table of Contents",
                         "href":contents_filename})
etree.SubElement(guide,"reference",
                 attrib={"type":"text",
                         "title":filename_to_headline['001.html'],
                         "href":'001.html'})

with open(opf_filename,"wb") as fp:
    opf_element_tree = etree.ElementTree(package)
    opf_element_tree.write(fp,
                           pretty_print=True,
                           encoding="utf-8",
                           xml_declaration=True)

# ========================================================================

with open("/dev/null","w") as null:
    try:
        call(['kindlegen','-c2','-o',mobi_filename,opf_filename])
    except OSError as e:
        if e.errno == errno.ENOENT:
            print("Warning: kindlegen was not on your path; not generating .mobi version")
        else:
            raise

 # vim: set expandtab :
