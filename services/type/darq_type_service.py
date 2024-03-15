# darqos
# Copyright (C) 2022-2023 David Arnold

from darq.services import storage
from darq.runtime.type import Type

import orjson
import os
import sys

import darq

KEY = "darq.typeservice.db"


# FIXME: clarify relationship between this and darq.runtime.type.Type
# FIXME: figure out how type implementations register themselves here too


class TypeDefinition:
    """Description of an object type."""

    def __init__(self):
        self.uti = 'public.base'
        self.name = ''
        self.description = ''
        self.implementation = ''
        return

    def get_uti(self) -> str:
        """Return the unique Uniform Type Identifier (UTI) for this type."""
        return self.uti

    def get_name(self) -> str:
        """Return the unique short name for this type."""
        return self.name

    def get_description(self) -> str:
        """Return a short description of the produced object type."""
        return self.description

    def get_implementation(self) -> str:
        """Return the storage URL for the type implementation."""
        return self.implementation

    @staticmethod
    def from_dict(d):
        """Create a type description from a dictionary."""
        td = TypeDefinition()
        td.uti = d.get("uti")
        td.name = d.get("name")
        td.description = d.get("description")
        td.implementation = d.get("implementation")
        return td


class TypeService(darq.Service):

    def __init__(self):
        """Constructor."""

        # FIXME: use fixed port number for now.  What to do here?
        darq.init_callbacks(darq.SelectEventLoop(), self)

        darq.log(darq.Facility.SERVICE, darq.Level.INFO,
                 "Starting Type service.")

        super().__init__()

        # Look up database.
        self._db = {}
        self._storage = storage.api()
        buf = self._storage.get(KEY)

        if len(buf) > 0:
            typedefs = orjson.loads(buf)
            for type_data in typedefs:
                typedef = TypeDefinition.from_dict(type_data)
                self._db[typedef.get_uti()] = typedef

        darq.open_port(self.port)
        return

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "type"

    def persist(self):
        """Save in-memory database to persistent storage."""

        buf = orjson.dumps(self._db.items())
        self._storage.set(KEY, buf)
        return

    def register(self, uti: str, name: str, description: str, url: str):
        """Register the factory implementation for a type.

        :param uti:
        :param name:
        :param description:
        :param url: Storage URL for type implementation"""

        td = TypeDefinition()
        td.uti = uti
        td.name = name
        td.description = description

        # FIXME: should this be a host OS path, for now?
        td.implementation = url

        self._db[uti] = td
        self.persist()
        return

    def deregister(self, uti: str):
        """Deregister the factory implementation for a type."""

        del self._db[uti]
        self.persist()
        return

    def create(self, uti: str):
        """Create a new instance of a type."""

        td = self._db.get(uti)
        if td is None:
            raise KeyError("No such type")

        buf = self._storage.get(td.implementation)

        path = f"/tmp/{td.uti}"
        f = open(path)
        f.write(buf)
        f.close()

        # FIXME: timing attach here: overwrite temporary file, given known name.

        pid = os.fork()
        if pid == 0:
            os.execv("python", [path])

        return

    def open(self, object_id: str):
        """Open an existing instance of a type."""

        # FIXME: needs implementing!

        pass

    def handle_request(self, request: dict):

        method = request.get("method")
        if method == "register":
            self.register()

        elif method == "deregister":
            self.deregister()

        elif method == "create":
            self.create()

        elif method == "open":
            self.open()

        else:
            super().handle_request(request)
        return

    def handle_shutdown(self):
        darq.log(darq.Facility.SERVICE, darq.Level.INFO,
                 "Type Service handled shutdown request.")
        return


def initialize(service: TypeService):
    """Populate the 'hard-wired' types."""

    reg = service.register

    # FIXME: add implementations of useful types here
    # FIXME: add any DarqOS-specific types here too

    t = reg("public.item", "Item")
    t = reg("public.content", "Content")
    t = reg("public.composite-content", "Composite Content")
    t = reg("public.data", "Data")
    t = reg("public.database", "Database")
    t = reg("public.calendar-event", "Calendar Event")
    t = reg("public.message", "Message")
    t = reg("public.presentation")
    t = reg("public.contact", "Contact")
    t = reg("public.archive", "Archive")
    t = reg("public.disk-image", "Disk Image")
    t = reg("public.text", "Text")
    t = reg("public.plain-text", "Plain Text")
    t = reg("public.utf8-plain-text", "UTF-8 Text")
    t = reg("public.utf16-external-plain-text", "UTF-16 Text with BOM")
    t = reg("public.utf16-plain-text", "UTF-16 Text LE")
    t = reg("public.rtf", "Rich Text (RTF)")
    t = reg("public.html", "HTML")
    t = reg("public.xml", "XML")
    t = reg("public.source-code", "Source Code")
    t = reg("public.c-source", "C Source Code")
    t = reg("public.objective-c-source", "ObjC Source Code")
    t = reg("public.c-plus-plus-source", "C++ SOurce Code")
    t = reg("public.objective-c-plus-plus-source-code", "ObjC++ Source Code")
    t = reg("public.c-header", "C Header")
    t = reg("public.c-plus-plus-header", "C++ Header")
    t = reg("com.sun.java-source", "Java Source Code")
    t = reg("public.script", "Script")
    t = reg("public.assembly-source", "Assembly Source Code")
    t = reg("com.netscape.javascript-source", "JavaScript Source Code")
    t = reg("public.shell-script", "Shell Script")
    t = reg("public.csh-script", "C-Shell Script")
    t = reg("public.perl-script", "PERL Script")
    t = reg("public.python-script", "Python Script")
    t = reg("public.ruby-script", "Ruby Script")
    t = reg("public.php-script", "PHP Script")
    t = reg("com.sun.java-web-start", "Java Web Start")
    t = reg("com.apple.applescript.text", "AppleScript Text")
    t = reg("com.apple.applescript.script", "AppleScript")
    t = reg("public.object-code", "Object Code")
    t = reg("com.apple.mach-o-binary", "Mach-O Binary")
    t = reg("com.apple.pef-binary", "PEF (CFM-based) Binary")
    t = reg("com.microsoft.windows-executable", "Microsoft Windows Application")
    t = reg("com.microsoft.windows-dynamic-link-library", "Microsoft Dynamic Link Library")
    t = reg("com.sun.java-class", "Java Class")
    t = reg("com.sun.java-archive", "Java Archive")
    t = reg("com.apple.quartz-composer-composition", "Quartz Composer Composition")
    t = reg("org.gnu.gnu-tar-archive", "GNU tar Archive")
    t = reg("public.tar-archive", "tar Archive")
    t = reg("org.gnu.gnu-zip-archive", "gip Archive")
    t = reg("org.gnu.gnu-zip-tar-archive", "Gzip tar Archive")
    t = reg("com.apple.binhex-archive", "BinHex Archive")
    t = reg("com.apple.macbinary-archive", "MacBinary Archive")
    t = reg("public.url", "Uniform Resource Locator")
    t = reg("public.file-url", "File URL")
    t = reg("public.url-name", "URL Name")
    t = reg("public.vcard", "vCard")
    t = reg("public.image", "Image")
    t = reg("public.fax", "Fax")
    t = reg("public.jpeg", "JPEG Image")
    t = reg("public.jpeg-2000", "JPEG-2000 Image")
    t = reg("public.tiff", "TIFF Image")
    t = reg("public.camera-raw-image", "Base type for RAW images")
    t = reg("com.apple.pict", "PICT Image")
    t = reg("com.apple.macpaint-image", "MacPaint Image")
    t = reg("public.png", "PNG Image")
    t = reg("public.xbitmap-image", "XBM Image")
    t = reg("com.apple.quicktime-image", "QuickTime Image")
    t = reg("com.apple.icns", "macOS Icon Image")

    return


def main():
    service = TypeService()
    #initialize(service)
    result = service.run()
    sys.exit(result)


if __name__ == "__main__":
    main()
