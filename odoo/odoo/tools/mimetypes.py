# -*- coding: utf-8 -*-
"""
Mimetypes-related utilities

# TODO: reexport stdlib mimetypes?
"""
import collections
import functools
import io
import logging
import mimetypes
import re
import zipfile

__all__ = ['guess_mimetype']

_logger = logging.getLogger(__name__)
_logger_guess_mimetype = _logger.getChild('guess_mimetype')

# We define our own guess_mimetype implementation and if magic is available we
# use it instead.

# discriminants for zip-based file formats
_ooxml_dirs = {
    'word/': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pt/': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xl/': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
def _check_ooxml(data):
    with io.BytesIO(data) as f, zipfile.ZipFile(f) as z:
        filenames = z.namelist()
        # OOXML documents should have a [Content_Types].xml file for early
        # check that we're interested in this thing at all
        if '[Content_Types].xml' not in filenames:
            return False

        # then there is a directory whose name denotes the type of the file:
        # word, pt (powerpoint) or xl (excel)
        for dirname, mime in _ooxml_dirs.items():
            if any(entry.startswith(dirname) for entry in filenames):
                return mime

        return False


# checks that a string looks kinda sorta like a mimetype
_mime_validator = re.compile(r"""
    [\w-]+ # type-name
    / # subtype separator
    [\w-]+ # registration facet or subtype
    (?:\.[\w-]+)* # optional faceted name
    (?:\+[\w-]+)? # optional structured syntax specifier
""", re.VERBOSE)
def _check_open_container_format(data):
    # Open Document Format for Office Applications (OpenDocument) Version 1.2
    #
    # Part 3: Packages
    # 3 Packages
    # 3.3 MIME Media Type
    with io.BytesIO(data) as f, zipfile.ZipFile(f) as z:
        # If a MIME media type for a document exists, then an OpenDocument
        # package should contain a file with name "mimetype".
        if 'mimetype' not in z.namelist():
            return False

        # The content of this file shall be the ASCII encoded MIME media type
        # associated with the document.
        marcel = z.read('mimetype').decode('ascii')
        # check that it's not too long (RFC6838 § 4.2 restricts type and
        # subtype to 127 characters each + separator, strongly recommends
        # limiting them to 64 but does not require it) and that it looks a lot
        # like a valid mime type
        if len(marcel) < 256 and _mime_validator.match(marcel):
            return marcel

        return False


_old_ms_office_mimetypes = {
    '.doc': 'application/msword',
    '.xls': 'application/vnd.ms-excel',
    '.ppt': 'application/vnd.ms-powerpoint',
}
_olecf_mimetypes = ('application/x-ole-storage', 'application/CDFV2')
_xls_pattern = re.compile(b"""
    \x09\x08\x10\x00\x00\x06\x05\x00
  | \xFD\xFF\xFF\xFF(\x10|\x1F|\x20|"|\\#|\\(|\\))
""", re.VERBOSE)
_ppt_pattern = re.compile(b"""
    \x00\x6E\x1E\xF0
  | \x0F\x00\xE8\x03
  | \xA0\x46\x1D\xF0
  | \xFD\xFF\xFF\xFF(\x0E|\x1C|\x43)\x00\x00\x00
""", re.VERBOSE)
def _check_olecf(data):
    """ Pre-OOXML Office formats are OLE Compound Files which all use the same
    file signature ("magic bytes") and should have a subheader at offset 512
    (0x200).

    Subheaders taken from http://www.garykessler.net/library/file_sigs.html
    according to which Mac office files *may* have different subheaders. We'll
    ignore that.
    """
    offset = 0x200
    if data.startswith(b'\xEC\xA5\xC1\x00', offset):
        return 'application/msword'
    # the _xls_pattern stuff doesn't seem to work correctly (the test file
    # only has a bunch of \xf* at offset 0x200), that apparently works
    elif b'Microsoft Excel' in data:
        return 'application/vnd.ms-excel'
    elif _ppt_pattern.match(data, offset):
        return 'application/vnd.ms-powerpoint'
    return False


def _check_svg(data):
    """This simply checks the existence of the opening and ending SVG tags"""
    if b'<svg' in data and b'/svg' in data:
        return 'image/svg+xml'

def _check_webp(data):
    """This checks the presence of the WEBP and VP8 in the RIFF"""
    if data[8:15] == b'WEBPVP8':
        return 'image/webp'

# for "master" formats with many subformats, discriminants is a list of
# functions, tried in order and the first non-falsy value returned is the
# selected mime type. If all functions return falsy values, the master
# mimetype is returned.
_Entry = collections.namedtuple('_Entry', ['mimetype', 'signatures', 'discriminants'])
_mime_mappings = (
    # pdf
    _Entry('application/pdf', [b'%PDF'], []),
    # jpg, jpeg, png, gif, bmp, jfif
    _Entry('image/jpeg', [b'\xFF\xD8\xFF\xE0', b'\xFF\xD8\xFF\xE2', b'\xFF\xD8\xFF\xE3', b'\xFF\xD8\xFF\xE1', b'\xFF\xD8\xFF\xDB'], []),
    _Entry('image/png', [b'\x89PNG\r\n\x1A\n'], []),
    _Entry('image/gif', [b'GIF87a', b'GIF89a'], []),
    _Entry('image/bmp', [b'BM'], []),
    _Entry('application/xml', [b'<'], [
        _check_svg,
    ]),
    _Entry('image/x-icon', [b'\x00\x00\x01\x00'], []),
    _Entry('image/webp', [b'RIFF'], [
        _check_webp,
    ]),
    # OLECF files in general (Word, Excel, PPT, default to word because why not?)
    _Entry('application/msword', [b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1', b'\x0D\x44\x4F\x43'], [
        _check_olecf
    ]),
    # zip, but will include jar, odt, ods, odp, docx, xlsx, pptx, apk
    _Entry('application/zip', [b'PK\x03\x04'], [_check_ooxml, _check_open_container_format]),
)
def _odoo_guess_mimetype(bin_data, default='application/octet-stream'):
    """ Attempts to guess the mime type of the provided binary data, similar
    to but significantly more limited than libmagic

    :param str bin_data: binary data to try and guess a mime type for
    :returns: matched mimetype or ``application/octet-stream`` if none matched
    """
    # by default, guess the type using the magic number of file hex signature (like magic, but more limited)
    # see http://www.filesignatures.net/ for file signatures
    for entry in _mime_mappings:
        for signature in entry.signatures:
            if bin_data.startswith(signature):
                for discriminant in entry.discriminants:
                    try:
                        guess = discriminant(bin_data)
                        if guess: return guess
                    except Exception:
                        # log-and-next
                        _logger_guess_mimetype.warning(
                            "Sub-checker '%s' of type '%s' failed",
                            discriminant.__name__, entry.mimetype,
                            exc_info=True
                        )
                # if no discriminant or no discriminant matches, return
                # primary mime type
                return entry.mimetype
    try:
        if bin_data and all(c >= ' ' or c in '\t\n\r' for c in bin_data[:1024].decode()):
            return 'text/plain'
    except ValueError:
        pass
    return default


try:
    import magic
except ImportError:
    magic = None

if magic:
    # There are 2 python libs named 'magic' with incompatible api.
    # magic from pypi https://pypi.python.org/pypi/python-magic/
    if hasattr(magic, 'from_buffer'):
        _guesser = functools.partial(magic.from_buffer, mime=True)
    # magic from file(1) https://packages.debian.org/squeeze/python-magic
    elif hasattr(magic, 'open'):
        ms = magic.open(magic.MAGIC_MIME_TYPE)
        ms.load()
        _guesser = ms.buffer

    def guess_mimetype(bin_data, default=None):
        mimetype = _guesser(bin_data[:1024])
        # upgrade incorrect mimetype to official one, fixed upstream
        # https://github.com/file/file/commit/1a08bb5c235700ba623ffa6f3c95938fe295b262
        if mimetype == 'image/svg':
            return 'image/svg+xml'
        # application/CDFV2 and application/x-ole-storage are two files
        # formats that Microsoft Office was using before 2006. Use our
        # own guesser to further discriminate the mimetype.
        if mimetype in _olecf_mimetypes:
            try:
                if msoffice_mimetype := _check_olecf(bin_data):
                    return msoffice_mimetype
            except Exception:  # noqa: BLE001
                _logger_guess_mimetype.warning(
                    "Sub-checker '_check_olecf' of type '%s' failed",
                    mimetype,
                    exc_info=True,
                )
        return mimetype
else:
    guess_mimetype = _odoo_guess_mimetype


def neuter_mimetype(mimetype, user):
    wrong_type = 'ht' in mimetype or 'xml' in mimetype or 'svg' in mimetype
    if wrong_type and not user._is_system():
        return 'text/plain'
    return mimetype


_extension_pattern = re.compile(r'\w+')
def get_extension(filename):
    # A file has no extension if it has no dot (ignoring the leading one
    # of hidden files) or that what follow the last dot is not a single
    # word, e.g. "Mr. Doe"
    _stem, dot, ext = filename.lstrip('.').rpartition('.')
    if not dot or not _extension_pattern.fullmatch(ext):
        return ''

    # Assume all 4-chars extensions to be valid extensions even if it is
    # not known from the mimetypes database. In /etc/mime.types, only 7%
    # known extensions are longer.
    if len(ext) <= 4:
        return f'.{ext}'.lower()

    # Use the mimetype database to determine the extension of the file.
    guessed_mimetype, guessed_ext = mimetypes.guess_type(filename)
    if guessed_ext:
        return guessed_ext
    if guessed_mimetype:
        return f'.{ext}'.lower()

    # Unknown extension.
    return ''


def fix_filename_extension(filename, mimetype):
    """
    Make sure the filename ends with an extension of the mimetype.

    :param str filename: the filename with an unsafe extension
    :param str mimetype: the mimetype detected reading the file's content
    :returns: the same filename if its extension matches the detected
        mimetype, otherwise the same filename with the mimetype's
        extension added at the end.
    """
    extension_mimetype = mimetypes.guess_type(filename)[0]
    if extension_mimetype == mimetype:
        return filename

    extension = get_extension(filename)
    if mimetype in _olecf_mimetypes and extension in _old_ms_office_mimetypes:
        return filename

    if mimetype == 'application/zip' and extension in {'.docx', '.xlsx', '.pptx'}:
        return filename

    if extension := mimetypes.guess_extension(mimetype):
        _logger.warning("File %r has an invalid extension for mimetype %r, adding %r", filename, mimetype, extension)
        return filename + extension

    _logger.warning("File %r has an unknown extension for mimetype %r", filename, mimetype)
    return filename
