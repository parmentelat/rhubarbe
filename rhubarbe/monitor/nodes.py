"""
The nodes monitor cyclically checks for the status of all nodes,
and reports it to the sidecar service
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import time
import re
import asyncio

from rhubarbe.config import Config
from rhubarbe.node import Node
from rhubarbe.ssh import SshProxy
# use a dedicated logger for monitors
from rhubarbe.logger import monitor_logger as logger

# connect to sidecar
from rhubarbe.monitor.sidecar import ReconnectableSidecar

# translate info into a single char for logging
def one_char_summary(info):
    if 'cmc_on_off' in info and info['cmc_on_off'] != 'on':
        return '.'
    if 'control_ping' in info and info['control_ping'] != 'on':
        return 'o'
    if 'control_ssh' in info and info['control_ssh'] != 'on':
        return '0'
    if 'os_release' in info and 'fedora' in info['os_release']:
        return 'F'
    if 'os_release' in info and 'ubuntu' in info['os_release']:
        return 'U'
    return '^'


class MonitorNode:
    """
    the logic for probing a node as part of monitoring
    formerly in fitsophia/website/monitor.py
    . first probes for the CMC status; if off: done
    . second probe for ssh; if on : done
    . third, checks for ping
    """

    def __init__(self, node, reconnectable,             # pylint: disable=r0913
                 report_wlan=False, verbose=False):
        # a rhubarbe.node.Node instance
        self.node = node
        self.report_wlan = report_wlan
        self.reconnectable = reconnectable
        self.verbose = verbose
        # current info - will be reported to sidecar
        self.info = {'id': node.id}
        # remember previous wlan measurement to compute rate
        self.history = {}

    def set_info(self, *overrides):
        """
        update self.info with all the dicts in overrides
        """
        for override in overrides:
            self.info.update(override)

    def zero_wlan_infos(self):
        """
        set wlan-related attributes to 0.
        """
        for k in self.info:
            if k.startswith('wlan'):
                self.info[k] = 0.

    async def report_info(self):
        """
        Send info to sidecar
        """
        await self.reconnectable.emit_info(self.info)

    async def set_info_and_report(self, *overrides):
        """
        Convenience to set info and immediately report
        """
        self.set_info(*overrides)
        await self.report_info()

    ubuntu_matcher = re.compile(r"DISTRIB_RELEASE=(?P<ubuntu_version>[0-9.]+)")
    fedora_matcher = re.compile(r"Fedora release (?P<fedora_version>\d+)")
    gnuradio_matcher = re.compile(
        r"\AGNURADIO:(?P<gnuradio_version>[0-9\.]+)\Z")
    uname_matcher = re.compile(r"\AUNAME:(?P<uname>.+)\Z")
    # 2016-05-28@08:20 - node fit38 - image oai-enb-base2 - by root
    rhubarbe_image_matcher = re.compile(
        r"\A/etc/rhubarbe-image:.* - image (?P<image_radical>[^ ]+) - by")
    rxtx_matcher = re.compile(
        r"==> /sys/class/net/wlan(?P<wlan_no>[0-9])/"
        r"statistics/(?P<rxtx>[rt]x)_bytes <==")
    number_matcher = re.compile(r"\A[0-9]+\Z")

    def parse_ssh_probe_output(self,      # pylint: disable=r0912, r0914, r0915
                               stdout, padding_dict):
        os_release = "other"
        gnuradio_release = "none"
        uname = ""
        rxtx_dict = {}
        rxtx_key = None
        image_radical = ""
        for line in stdout.split("\n"):
            match = self.ubuntu_matcher.match(line)
            if match:
                version = match.group('ubuntu_version')
                os_release = "ubuntu-{version}".format(version=version)
                continue
            match = self.fedora_matcher.match(line)
            if match:
                version = match.group('fedora_version')
                os_release = "fedora-{version}".format(version=version)
                continue
            match = self.gnuradio_matcher.match(line)
            if match:
                gnuradio_release = match.group('gnuradio_version')
                continue
            match = self.uname_matcher.match(line)
            if match:
                uname = match.group('uname')
                continue
            match = self.rhubarbe_image_matcher.match(line)
            if match:
                image_radical = match.group('image_radical')
                continue
            match = self.rxtx_matcher.match(line)
            if match:
                # use a tuple as the hash
                rxtx_key = (match.group('wlan_no'), match.group('rxtx'))
                continue
            match = self.number_matcher.match(line)
            if match and rxtx_key:
                rxtx_dict[rxtx_key] = int(line)
                continue
            rxtx_key = None

        # now that we have the counters we need to translate this into rate
        # for that purpose we use local clock;
        # small imprecision should not impact overall result
        now = time.time()
        wlan_info_dict = {}
        for rxtx_key, bytes in rxtx_dict.items():       # pylint: disable=w0622
            wlan_no, rxtx = rxtx_key
            # rather dirty hack for images that use wlan2 and wlan3
            # expose in wlan0 or wlan1 depending on parity of actual device
            try:
                wlan_no = int(wlan_no)
                wlan_no = wlan_no % 2
            except Exception:
                pass
            if self.verbose:
                logger.info("node={node} collected {bytes} "
                            "for device wlan{wlan_no} in {rxtx}"
                            .format(node=self.node, bytes=bytes,
                                    wlan_no=wlan_no, rxtx=rxtx))
            # do we have something on this measurement ?
            if rxtx_key in self.history:
                previous_bytes, previous_time = self.history[rxtx_key]
                info_key = "wlan_{wlan_no}_{rxtx}_rate".format(**locals())
                new_rate = 8.*(bytes - previous_bytes) / (now - previous_time)
                wlan_info_dict[info_key] = new_rate
                if self.verbose:
                    logger.info("node={} computed {} bps for key {} "
                                "- bytes = {}, pr = {}, now = {}, pr = {}"
                                .format(id, new_rate, info_key,
                                        bytes, previous_bytes,
                                        now, previous_time))
            # store this measurement for next run
            self.history[rxtx_key] = (bytes, now)
        # xxx would make sense to clean up history for measurements that
        # we were not able to collect at this cycle
        self.set_info({'os_release': os_release,
                       'gnuradio_release': gnuradio_release,
                       'uname': uname,
                       'image_radical': image_radical},
                      padding_dict, wlan_info_dict)

    async def probe(self,                 # pylint: disable=r0912, r0914, r0915
                    ping_timeout, ssh_timeout):
        """
        The logic for getting one node's info and send it to sidecar
        """
        if self.verbose:
            logger.info("entering pass1, info={}".format(self.info))
        # pass1 : check for status
        padding_dict = {
            'control_ping': 'off',
            'control_ssh': 'off',
            # don't overwrite os_release though
        }
        # get USRP status no matter what - use "" if we receive None
        # to limit noise when the node is physically removed
        usrp_status = await self.node.get_usrpstatus() or 'fail'
        # replace usrpon and usrpoff with just on and off
        self.set_info({'usrp_on_off': usrp_status.replace('usrp', '')})
        # get CMC status
        status = await self.node.get_status()
        if status == "off":
            await self.set_info_and_report({'cmc_on_off': 'off'}, padding_dict)
            return
        elif status != "on":
            await self.set_info_and_report({'cmc_on_off': 'fail'}, padding_dict)
            return
        if self.verbose:
            logger.info("entering pass2, info={}".format(self.info))
        # pass2 : CMC status is ON - let's try to ssh it
        self.set_info({'cmc_on_off': 'on'})
        padding_dict = {
            'control_ping': 'on',
            'control_ssh': 'on',
        }
        self.zero_wlan_infos()
        remote_commands = [
            "cat /etc/lsb-release /etc/fedora-release /etc/gnuradio-release "
            "2> /dev/null | grep -i release",
            "echo -n GNURADIO: ; gnuradio-config-info --version "
            "2> /dev/null || echo none",
            # this trick allows to have the filename on each output line
            "grep . /etc/rhubarbe-image /dev/null",
            "echo -n 'UNAME:' ; uname -r",
            ]
        if self.report_wlan:
            remote_commands.append(
                "head /sys/class/net/wlan?/statistics/[rt]x_bytes"
            )
        # reconnect each time
        async with SshProxy(self.node) as ssh:
            if self.verbose:
                logger.info("trying to ssh-connect (timeout={})"
                            .format(ssh_timeout))
            try:
                connected = await asyncio.wait_for(ssh.connect(),
                                                   timeout=ssh_timeout)
            except asyncio.TimeoutError:
                connected = False
            if self.verbose:
                logger.info("ssh-connected={}".format(connected))
            if connected:
                try:
                    command = ";".join(remote_commands)
                    output = await ssh.run(command)
                    # padding dict here sets control_ssh and control_ping to on
                    self.parse_ssh_probe_output(output, padding_dict)
                    # required as otherwise we leak openfiles
                    try:
                        await ssh.close()
                    except Exception:
                        logger.exception("monitornodes oops 1")
                except Exception:
                    logger.exception("monitornodes remote_command failed")
            else:
                self.set_info({'control_ssh': 'off'})

        # if we could ssh then we're done
        if self.info['control_ssh'] == 'on':
            await self.report_info()
            return

        if self.verbose:
            logger.info("entering pass3, info={}".format(self.info))
        # pass3 : node is ON but could not ssh
        # check for ping
        # I don't know of an asyncio library to deal with icmp
        # so let's use asyncio.subprocess
        # xxx maybe a Ping class would be the way to go
        control = self.node.control_hostname()
        command = ["ping", "-c", "1", "-t", "1", control]
        try:
            subprocess = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            # failure occurs through timeout
            await asyncio.wait_for(subprocess.wait(),
                                   timeout=ping_timeout)
            await self.set_info_and_report({'control_ping': 'on'})
            return
        except asyncio.TimeoutError:
            await self.set_info_and_report({'control_ping': 'off'})
            return

    async def probe_forever(self, cycle, ping_timeout, ssh_timeout):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            try:
                await self.probe(ping_timeout, ssh_timeout)
            except Exception:
                logger.exception("monitornodes oops 2")
            await asyncio.sleep(cycle)


class MonitorNodes:                                     # pylint: disable=r0902

    def __init__(self, cmc_names, message_bus,          # pylint: disable=r0913
                 sidecar_url, cycle,
                 report_wlan=False, verbose=False):
        self.cycle = cycle
        self.report_wlan = report_wlan
        self.verbose = verbose

        # get miscell config
        self.ping_timeout = float(Config().value('networking', 'ping_timeout'))
        self.ssh_timeout = float(Config().value('networking', 'ssh_timeout'))
        self.log_period = float(Config().value('monitor', 'log_period'))

        # websockets
        self.reconnectable = \
            ReconnectableSidecar(sidecar_url, 'nodes')

        # the nodes part
        nodes = [Node(cmc_name, message_bus) for cmc_name in cmc_names]
        self.monitor_nodes = [
            MonitorNode(node=node, reconnectable=self.reconnectable,
                        report_wlan=self.report_wlan,
                        verbose=verbose)
            for node in nodes]

    def on_channel(self, channel, *args):
        if channel == BACK_CHANNEL:
            self.monitor_leases.on_back_channel(*args)
        else:
            logger.error("received data on unexpected channel {}"
                         .format(channel))

    async def run_forever(self):
        logger.info("Starting nodes on {} nodes - report_wlan={}"
                    .format(len(self.monitor_nodes), self.report_wlan))
        # run n+1 tasks in parallel
        # one for leases,
        # plus one per node
        return asyncio.gather(
            *[monitor_node.probe_forever(self.cycle,
                                         ping_timeout=self.ping_timeout,
                                         ssh_timeout=self.ssh_timeout)
              for monitor_node in self.monitor_nodes]
        )

    async def log(self):
        previous = 0
        while True:
            line = "".join([one_char_summary(mnode.info)
                            for mnode in self.monitor_nodes])
            current = self.reconnectable.get_counter()
            delta = "+ {}".format(current-previous)
            line += " {} emits ({})".format(current, delta)
            previous = current
            logger.info(line)
            await asyncio.sleep(self.log_period)


if __name__ == '__main__':

    def main():
        # rebootnames = sys.argv[1:]
        message_bus = asyncio.Queue()

        test_url = Config().value('sidecar', 'url')
        reconnectable = ReconnectableSidecar(
            test_url, 'nodes')
        monitor_nodes = MonitorNodes(
            ["reboot01", "reboot02"],
            message_bus,
            test_url)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(monitor_nodes.run_forever())

    main()
