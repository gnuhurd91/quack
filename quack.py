#!/usr/bin/env python3

import os
import re
import sys
import time
import requests
import tempfile
import subprocess
from argparse import ArgumentParser
from configparser import ConfigParser


USE_COLOR = "never"


def hilite(string, color=None, bold=False, underline=False):
    if USE_COLOR == "never":
        return string
    attr = []
    color_map = {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36"
    }
    if color in color_map:
        attr.append(color_map[color])
    if bold:
        attr.append('1')
    if underline:
        attr.append('4')
    return "\x1b[{}m{}\x1b[0m".format(";".join(attr), string)


def print_error(message):
    print("{} {}".format(hilite("error :", "red", True), message),
          file=sys.stderr)
    sys.exit(1)


def print_info(message):
    print("{} {}".format(hilite("::", "blue"),
                         hilite(message, bold=True)))


def question(message):
    return input("{} {} ".format(
        hilite("::", "blue"),
        hilite(message, bold=True))).lower()


class AurHelper:
    def __init__(self, config):
        self.config = config
        self.local_pkgs = subprocess.run(
            ["pacman", "-Q", "--color=never"],
            check=True, stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE).stdout.decode().strip().split("\n")
        self.all_pkgs = subprocess.run(
            ["pacman", "--color=never", "-Slq"] + self.config["repos"],
            check=True, stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE).stdout.decode().strip().split("\n")

        self.editor = "nano"
        if os.getenv("EDITOR") != "":
            self.editor = os.getenv("EDITOR")

    def is_devel(self, package):
        return re.search("-(?:bzr|cvs|git|hg|svn)$", package)

    def clean_pkg_name(self, package):
        m = re.match("^aur/([a-z0-9-_]+)$", package)
        if m is None:
            return package
        return m[1]

    def current_version(self, package):
        for line in self.local_pkgs:
            m = re.match("^{} (.+)$".format(package), line)
            if m is None:
                continue
            return m[1]
        return None

    def color_pkg_with_version(self, package, version):
        version = hilite(version, "green", True)
        if self.current_version(package) is not None:
            version += " {}".format(hilite("[installed]", "cyan", True))
        return "{}{} {}".format(
            hilite("aur/", "magenta", True),
            hilite(package, bold=True),
            version)

    def list(self, with_version=False, with_devel=False):
        pkgs = []
        for p in self.local_pkgs:
            d = p.split(" ")
            if d[0] in self.all_pkgs:
                continue
            if self.is_devel(d[0]) and not with_devel:
                continue
            if with_version:
                pkgs.append(self.color_pkg_with_version(d[0], d[1]))
            else:
                pkgs.append(d[0])
        return pkgs

    def print_list(self, with_devel=False):
        print("\n".join(self.list(True, with_devel)))

    def fetch_results(self, terms, name_only=False):
        req = "https://aur.archlinux.org/rpc.php?v=5&type=info"
        params = ["arg[]={}".format(t) for t in terms]
        if name_only:
            params.append("&by=name")
        req = "{}&{}".format(req, "&".join(params))
        raw_json = requests.get(req).json()
        # Ensure we get a list
        if "results" not in raw_json:
            return []
        return raw_json["results"]

    def upgrade(self, with_devel=False):
        res = self.fetch_results(self.list(False, with_devel))
        if res is None or len(res) == 0:
            return False
        upgradable_pkgs = []
        for p in res:
            cur_version = self.current_version(p["Name"])
            if cur_version == p["Version"]:
                continue
            ver_check = [cur_version, p["Version"]]
            ver_check.sort()
            if (with_devel is False or self.is_devel(p["Name"]) is None) \
               and ver_check[1] == cur_version:
                # Somehow we have a local version greater than upstream
                continue
            upgradable_pkgs.append(p["Name"])
            print("{} - {} - {}".format(
                  hilite(p["Name"], bold=True),
                  hilite(cur_version, "red"),
                  hilite(p["Version"], "green")))
        if len(upgradable_pkgs) == 0:
            return True
        upcheck = question("Do you want to upgrade the "
                           "above packages? [y/N]")
        if upcheck != "y":
            return True
        for p in upgradable_pkgs:
            self.install(p)

    def pacman_install(self, packages):
        pacman_cmd = ["sudo", "pacman", "--color", USE_COLOR, "-U"]
        pkg_dest = "/var/cache/pacman/pkg"
        success = True
        p = subprocess.run(pacman_cmd + packages)
        if p.returncode != 0:
            # Strange, pacman failed. May be a sudo timeout. Keep a copy
            # of the pkgs
            pkg_dest = "/tmp"
            success = False
            print_info("A copy of the built packages has been kept in /tmp.")
        for p in packages:
            cmd = ["cp", p, "{}/{}".format(pkg_dest, p)]
            if success:
                cmd.insert(0, "sudo")
            subprocess.run(cmd)
        return success

    def install(self, package):
        package = self.clean_pkg_name(package)
        with tempfile.TemporaryDirectory() as tmpdirname:
            os.chdir(tmpdirname)
            p = subprocess.run(["git", "clone",
                                "https://aur.archlinux.org/{}.git"
                                .format(package)])
            if p.returncode != 0:
                print_error("impossible to clone {} from AUR"
                            .format(package))

            os.chdir(package)
            if not os.path.isfile("PKGBUILD"):
                print_error("{} is NOT an AUR package".format(package))

            print_info("Package {0} is ready to be built in {1}/{0}"
                       .format(package, tmpdirname))
            print_info("You should REALLY take time to inspect "
                       "its PKGBUILD.")
            check = question("When it's done, shall we continue? [y/N/q]")
            if check == "q":
                sys.exit()
            elif check != "y":
                return False
            p = subprocess.run(["makepkg", "-sr"])
            if p.returncode != 0:
                return False
            built_packages = []
            for f in os.listdir():
                if f.endswith(".pkg.tar.xz"):
                    built_packages.append(f)
            if len(built_packages) == 1:
                return self.pacman_install([built_packages[0]])
            print_info("The following packages have been built:")
            i = 0
            for l in built_packages:
                i += 1
                print("[{}] {}".format(i, l))
            ps = question("Which one do you really want to install?"
                          " [1…{}/A]".format(i))
            if ps == "a":
                return self.pacman_install(built_packages)
            final_pkgs = []
            try:
                for p in ps.split(" "):
                    pi = int(p)
                    if pi > len(built_packages):
                        raise ValueError
                    final_pkgs.append(built_packages[pi - 1])
            except ValueError:
                print_error("{} is not a valid input".format(p))
            return self.pacman_install(final_pkgs)

    def search(self, terms_str):
        req = "https://aur.archlinux.org/rpc.php?v=5&type=search&arg={}" \
              .format(terms_str)
        raw_json = requests.get(req).json()
        if "results" not in raw_json:
            return False

        for p in raw_json["results"]:
            print("{}\n    {}".format(
                self.color_pkg_with_version(p["Name"], p["Version"]),
                p["Description"]))

    def info_line(self, title, obj, alt_title=None):
        value = "--"
        if title in obj:
            if type(obj[title]) is list:
                if len(obj[title]) != 0:
                    value = "  ".join(obj[title])
            else:
                value = obj[title]
        if alt_title is not None:
            title = alt_title
        print("{}: {}".format(hilite(title, bold=True).ljust(33),
                              value))

    def info(self, package):
        package = self.clean_pkg_name(package)
        if self.current_version(package) is not None:
            p = subprocess.run(["pacman", "--color", USE_COLOR,
                                "-Qi", package])
            sys.exit(p.returncode)
        res = self.fetch_results([package], True)[0]
        if res is None:
            return False
        if "Maintainer" in res:
            uri_m = "{0}  https://aur.archlinux.org/account/{0}" \
                .format(res["Maintainer"])
            res["Last Maintainer"] = uri_m
        res["Last Modified"] = time.strftime(
            "%c %Z", time.gmtime(res["LastModified"]))
        res["AUR Page"] = "https://aur.archlinux.org/packages/{}" \
            .format(res["Name"])
        for t in ["Name", "Version", "Description", "URL", "License",
                  "Provides", "Depends", "MakeDepends", "Conflicts",
                  "Last Maintainer", "Last Modified", "NumVotes",
                  "Popularity", "AUR Page", "Keywords"]:
            if t in ["Depends", "MakeDepends"] and t in res:
                for p in res[t]:
                    if p in self.all_pkgs:
                        continue
                    res[t][res[t].index(p)] = hilite(p, underline=True)
            if t == "Depends":
                self.info_line(t, res, "Depends On")
            elif t == "MakeDepends":
                self.info_line(t, res, "Make Depends On")
            elif t == "Conflicts":
                self.info_line(t, res, "Conflicts With")
            elif t == "NumVotes":
                self.info_line(t, res, "Votes Number")
            else:
                self.info_line(t, res)
        print()  # pacman -Qi print one last line


if __name__ == "__main__":
    parser = ArgumentParser(description="Yet Another Pacman Wrapper")
    parser.add_argument("--color", help="Specify when to enable "
                        "coloring. Valid options are always, "
                        "never, or auto.")
    cmd_group = parser.add_argument_group("Operations")
    cmd_group.add_argument("-C", "--list-garbage", action="store_true",
                           help="Find and list .pacsave, "
                           ".pacorig, .pacnew files")
    cmd_group.add_argument("-A", "--aur", action="store_true",
                           help="AUR related operations "
                           "(default to install package)")
    sub_group = parser.add_argument_group("AUR options")
    sub_group.add_argument("-l", "--list", action="store_true",
                           help="List locally installed AUR packages "
                           "and exit.")
    sub_group.add_argument("-u", "--upgrade", action="store_true",
                           help="Upgrade locally installed AUR packages.")
    sub_group.add_argument("-s", "--search", action="store_true",
                           help="Search AUR packages by name and exit.")
    sub_group.add_argument("-i", "--info", action="store_true",
                           help="Display information on an AUR package "
                           "and exit.")
    parser.add_argument("--devel", action="store_true",
                        help="Include devel packages "
                        "(which name has a trailing -svn, -git…) "
                        "for list and upgrade operations")
    parser.add_argument("package", nargs="*", default=[])
    args = parser.parse_args()

    config = {
        "color": "never",
        "repos": []
    }

    if os.path.isfile("/etc/pacman.conf"):
        pac_conf = ConfigParser(allow_no_value=True)
        pac_conf.read("/etc/pacman.conf")
        config["repos"] = pac_conf.sections()
        config["repos"].remove("options")
        if "options" in pac_conf and "Color" in pac_conf["options"]:
            if pac_conf["options"]["Color"] is None:
                USE_COLOR = "auto"
            else:
                USE_COLOR = pac_conf["options"]["Color"].lower()

    if args.color:
        ac = args.color.lower()
        if ac in ["never", "auto", "always"]:
            USE_COLOR = ac
    config["color"] = USE_COLOR

    if args.list_garbage:
        print_info("Pacman post transaction files")
        ignore_pathes = [
            "/dev", "/home", "/lost+found", "/proc", "/root",
            "/run", "/sys", "/tmp", "/var/db", "/var/log",
            "/var/spool", "/var/tmp"
        ]
        cmd = ["find", "/", "("]
        for p in ignore_pathes:
            cmd.extend(["-path", p, "-o"])
        cmd.pop()
        if os.getuid() != 0:
            cmd.insert(0, "sudo")
        cmd += [")", "-prune", "-o", "-type", "f", "("]
        for p in ["*.pacsave", "*.pacorig", "*.pacnew"]:
            cmd.extend(["-name", p, "-o"])
        cmd.pop()
        cmd += [")", "-print"]
        subprocess.run(cmd)
        print_info("Orphaned packages")
        subprocess.run(["pacman", "--color", USE_COLOR, "-Qdt"])
        sys.exit()

    if os.getuid() == 0:
        print_error("Do not run {} as root!".format(sys.argv[0]))

    aur = AurHelper(config)

    have_subcommand = args.search or args.info or args.list or args.upgrade
    if not args.aur or (have_subcommand is False and len(args.package) == 0):
        print_error("No operation given")
        sys.exit(1)

    if args.search:
        aur.search(" ".join(args.package))

    elif args.info:
        aur.info(" ".join(args.package))

    elif args.list:
        aur.print_list(args.devel)

    elif args.upgrade:
        aur.upgrade(args.devel)

    else:
        for p in args.package:
            aur.install(p)
