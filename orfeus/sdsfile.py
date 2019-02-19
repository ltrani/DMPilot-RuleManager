"""

  orfeus/sdsfile class for handling SDS type files.

  Author: Mathijs Koymans, 2017
  Copyright: ORFEUS Data Center, 2017
  Modified: 2019

"""

import os
import json
import requests
import subprocess

from datetime import datetime, timedelta
from hashlib import sha256

from configuration import config


class SDSFile():

    """
    Public Class SDSFile
    Class for handling files in SDS archive
    """

    # Same some configuration to the class
    archive = config["ARCHIVE_ROOT"]
    fdsnws = config["FDSNWS_ADDRESS"]

    def __init__(self, filename):
        """
        Create a filestream from a given filename
        """

        self.filename = filename

        # Extract stream identification
        (self.net,
         self.sta,
         self.loc,
         self.cha,
         self.quality,
         self.year,
         self.day) = filename.split(".")

    # Returns filepath for a given file
    @property
    def filepath(self):
        return os.path.join(self.directory, self.filename)

    # Returns the stream identifier
    @property
    def id(self):
        return ".".join([
            self.net,
            self.sta,
            self.loc,
            self.cha
        ])

    # Returns the file directory based on SDS structure
    @property
    def directory(self):
        return os.path.join(
            self.archive,
            self.year,
            self.net,
            self.sta,
            self.channelDirectory
        )

    # Returns channel directory
    @property
    def channelDirectory(self):
        return ".".join([self.cha, self.quality])

    # Returns next file in stream
    @property
    def next(self):
        return self.__getAdjacentFile(1)

    # Returns previous file in stream
    @property
    def previous(self):
        return self.__getAdjacentFile(-1)

    # Returns start time of file
    @property
    def start(self):
        return datetime.strptime(self.year + " " + self.day, "%Y %j")

    # Returns end time of file
    @property
    def end(self):
        return self.start + timedelta(days=1)

    # Start for dataselect pruning (start is INCLUSIVE)
    @property
    def sampleStart(self):
        return self.start.strftime("%Y,%j,00,00,00.000000")

    # End for dataselect pruning (end is INCLUSIVE)
    @property
    def sampleEnd(self):
        return self.start.strftime("%Y,%j,23,59,59.999999")

    # Returns the file path of file
    @property
    def filepath(self):
        return os.path.join(self.archive, self.directory, self.filename)

    # Returns list of files neighbouring a file
    @property
    def neighbours(self):
        return filter(lambda x: os.path.isfile(x.filepath), [self.previous, self, self.next])

    @property
    def stats(self):
        return os.stat(self.filepath)

    @property
    def size(self):
        return self.stats.st_size

    @property
    def created(self):
        return datetime.fromtimestamp(self.stats.st_ctime)

    @property
    def modified(self):
        return datetime.fromtimestamp(self.stats.st_mtime)

    @property
    def checksum(self):
        """
        def SDSFile::checksum
        Calculates the SHA256 checksum for a given file
        """

        checksum = sha256()
        with open(self.filepath, "rb") as f:
            for block in iter(lambda: f.read(0x10000), b""):
                checksum.update(block)
        return checksum.hexdigest()

    @property
    def queryString(self):
        """
        def SDSFile::queryString
        Returns the query string for a particular SDS file
        """

        return "?" + "&".join([
            "start=%s" % self.start,
            "end=%s" % self.end,
            "network=%s" % self.net,
            "station=%s" % self.sta,
            "location=%s" % self.loc,
            "channel=%s" % self.cha,
            "format=text",
            "level=channel"
        ])

    @property
    def samples(self):
        """
        def SDSFile::samples
        Returns number of samples in the SDS file
        """

        return sum(map(lambda x: x["samples"], self.traces))

    @property
    def continuous(self):
        """
        def SDSFile::continuous
        Returns True when a SDS file is considered continuous and the
        record start time & end time come before and after the file ending respectively
        """

        return len(
            self.traces) == 1 and (
            self.traces[0]["start"] <= self.start) and (
            self.traces[0]["end"] >= self.end)

    @property
    def traces(self):
        """
        def SDSFile::traces
        Returns a list of traces
        """

        def parseMSIOutput(line):
            """
            def SDSFile::traces::parseMSIOutput
            Parses the MSI output
            """

            # Format for seed dates e.g. 2005,068,00:00:01.000000
            SEED_DATE_FMT = "%Y,%j,%H:%M:%S.%f"

            (stream, start, end, rate, samples) = line.split()

            # Return a simple dict with some information
            return {
                "samples": int(samples),
                "rate": float(rate),
                "start": datetime.strptime(start, SEED_DATE_FMT),
                "end": datetime.strptime(end, SEED_DATE_FMT)
            }

        # Get the output of the subprocess
        lines = subprocess.check_output([
            "msi",
            "-ts", self.sampleStart,
            "-te", self.sampleEnd,
            "-T"
        ] + map(lambda x: x.filepath, self.neighbours)).splitlines()

        # Skip first header & final line
        return map(parseMSIOutput, lines[1:-1])

    @property
    def location(self):
        """
        def SDSFile::location
        Returns the geographical location of the stream
        """

        # Query our FDSNWS Webservice for the station location
        request = requests.get(os.path.join(self.fdsnws, self.queryString))

        # Any error just ignore
        if request.status_code != 200:
            return None

        lines = request.text.split("\n")

        # Multiple lines means that the location is somehow ambiguous
        if len(lines) != 3:
            return None

        # Some magic parsing: fields 4, 5, 6 on the 2nd line
        (latitude, longitude, elevation) = lines[1].split("|")[4:7]

        return {
            "longitude": float(longitude),
            "latitude": float(latitude),
            "elevation": float(elevation)
        }

    def prune(self):
        """
        def SDSFile::prune
        Uses IRIS dataselect to prune a file on the sample level
        and sets the quality indicator to Q (modified)
        """

        # Set quality indicator
        self.quality = "Q"

        # Write pruned data to /dev/null for now
        subprocess.call([
            "dataselect",
            "-Ps",
            "-Q Q",
            "-ts", self.sampleStart,
            "-te", self.sampleEnd,
            "-o", "/dev/null"
        ] + map(lambda x: x.filepath, self.neighbours))

    def __getAdjacentFile(self, direction):
        """
        def SDSFile::__getAdjacentFile
        Private function that returns adjacent SDSFile based on direction
        """

        newDate = self.start + timedelta(days=direction)

        # The year and day may change
        newYear = newDate.strftime("%Y")
        newDay = newDate.strftime("%j")

        newFilename = ".".join([
            self.net,
            self.sta,
            self.loc,
            self.cha,
            self.quality,
            newYear,
            newDay
        ])

        return SDSFile(newFilename)
