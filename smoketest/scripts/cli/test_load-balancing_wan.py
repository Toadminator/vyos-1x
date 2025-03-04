#!/usr/bin/env python3
#
# Copyright (C) 2022-2024 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import unittest
import time

from base_vyostest_shim import VyOSUnitTestSHIM
from vyos.utils.file import chmod_755
from vyos.utils.file import write_file
from vyos.utils.process import call
from vyos.utils.process import cmd

base_path = ['load-balancing']

def create_netns(name):
    return call(f'sudo ip netns add {name}')

def create_veth_pair(local='veth0', peer='ceth0'):
    return call(f'sudo ip link add {local} type veth peer name {peer}')

def move_interface_to_netns(iface, netns_name):
    return call(f'sudo ip link set {iface} netns {netns_name}')

def rename_interface(iface, new_name):
    return call(f'sudo ip link set {iface} name {new_name}')

def cmd_in_netns(netns, cmd):
    return call(f'sudo ip netns exec {netns} {cmd}')

def delete_netns(name):
    return call(f'sudo ip netns del {name}')

class TestLoadBalancingWan(VyOSUnitTestSHIM.TestCase):
    @classmethod
    def setUpClass(cls):
        super(TestLoadBalancingWan, cls).setUpClass()

        # ensure we can also run this test on a live system - so lets clean
        # out the current configuration :)
        cls.cli_delete(cls, base_path)

    def tearDown(self):
        self.cli_delete(base_path)
        self.cli_commit()

        removed_chains = [
            'wlb_mangle_isp_veth1',
            'wlb_mangle_isp_veth2',
            'wlb_mangle_isp_eth201',
            'wlb_mangle_isp_eth202'
        ]

        for chain in removed_chains:
            self.verify_nftables_chain_exists('ip vyos_wanloadbalance', chain, inverse=True)

    def test_table_routes(self):
        ns1 = 'ns201'
        ns2 = 'ns202'
        ns3 = 'ns203'
        iface1 = 'eth201'
        iface2 = 'eth202'
        iface3 = 'eth203'
        container_iface1 = 'ceth0'
        container_iface2 = 'ceth1'
        container_iface3 = 'ceth2'

        # Create network namespeces
        create_netns(ns1)
        create_netns(ns2)
        create_netns(ns3)
        create_veth_pair(iface1, container_iface1)
        create_veth_pair(iface2, container_iface2)
        create_veth_pair(iface3, container_iface3)

        move_interface_to_netns(container_iface1, ns1)
        move_interface_to_netns(container_iface2, ns2)
        move_interface_to_netns(container_iface3, ns3)
        call(f'sudo ip address add 203.0.113.10/24 dev {iface1}')
        call(f'sudo ip address add 192.0.2.10/24 dev {iface2}')
        call(f'sudo ip address add 198.51.100.10/24 dev {iface3}')
        call(f'sudo ip link set dev {iface1} up')
        call(f'sudo ip link set dev {iface2} up')
        call(f'sudo ip link set dev {iface3} up')
        cmd_in_netns(ns1, f'ip link set {container_iface1} name eth0')
        cmd_in_netns(ns2, f'ip link set {container_iface2} name eth0')
        cmd_in_netns(ns3, f'ip link set {container_iface3} name eth0')
        cmd_in_netns(ns1, 'ip address add 203.0.113.1/24 dev eth0')
        cmd_in_netns(ns2, 'ip address add 192.0.2.1/24 dev eth0')
        cmd_in_netns(ns3, 'ip address add 198.51.100.1/24 dev eth0')
        cmd_in_netns(ns1, 'ip link set dev eth0 up')
        cmd_in_netns(ns2, 'ip link set dev eth0 up')
        cmd_in_netns(ns3, 'ip link set dev eth0 up')

        # Set load-balancing configuration
        self.cli_set(base_path + ['wan', 'hook', '/bin/true'])
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'failure-count', '2'])
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'nexthop', '203.0.113.1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'success-count', '1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'failure-count', '2'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'nexthop', '192.0.2.1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'success-count', '1'])

        self.cli_set(base_path + ['wan', 'rule', '10', 'inbound-interface', iface3])
        self.cli_set(base_path + ['wan', 'rule', '10', 'source', 'address', '198.51.100.0/24'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', iface1])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', iface2])

        # commit changes
        self.cli_commit()

        time.sleep(5)
        # Check default routes in tables 201, 202
        # Expected values
        original = 'default via 203.0.113.1 dev eth201'
        tmp = cmd('sudo ip route show table 201')
        self.assertEqual(tmp, original)

        original = 'default via 192.0.2.1 dev eth202'
        tmp = cmd('sudo ip route show table 202')
        self.assertEqual(tmp, original)

        # Delete veth interfaces and netns
        for iface in [iface1, iface2, iface3]:
            call(f'sudo ip link del dev {iface}')

        delete_netns(ns1)
        delete_netns(ns2)
        delete_netns(ns3)

    def test_check_chains(self):
        ns1 = 'nsA'
        ns2 = 'nsB'
        ns3 = 'nsC'
        iface1 = 'veth1'
        iface2 = 'veth2'
        iface3 = 'veth3'
        container_iface1 = 'ceth0'
        container_iface2 = 'ceth1'
        container_iface3 = 'ceth2'
        mangle_isp1 = """table ip vyos_wanloadbalance {
	chain wlb_mangle_isp_veth1 {
		meta mark set 0x000000c9 ct mark set 0x000000c9 counter accept
	}
}"""
        mangle_isp2 = """table ip vyos_wanloadbalance {
	chain wlb_mangle_isp_veth2 {
		meta mark set 0x000000ca ct mark set 0x000000ca counter accept
	}
}"""
        mangle_prerouting = """table ip vyos_wanloadbalance {
	chain wlb_mangle_prerouting {
		type filter hook prerouting priority mangle; policy accept;
		iifname "veth3" ip saddr 198.51.100.0/24 ct state new limit rate 5/second burst 5 packets counter numgen random mod 11 vmap { 0 : jump wlb_mangle_isp_veth1, 1-10 : jump wlb_mangle_isp_veth2 }
		iifname "veth3" ip saddr 198.51.100.0/24 counter meta mark set ct mark
	}
}"""
        nat_wanloadbalance = """table ip vyos_wanloadbalance {
	chain wlb_nat_postrouting {
		type nat hook postrouting priority srcnat - 1; policy accept;
		ct mark 0x000000c9 counter snat to 203.0.113.10
		ct mark 0x000000ca counter snat to 192.0.2.10
	}
}"""

        # Create network namespeces
        create_netns(ns1)
        create_netns(ns2)
        create_netns(ns3)
        create_veth_pair(iface1, container_iface1)
        create_veth_pair(iface2, container_iface2)
        create_veth_pair(iface3, container_iface3)
        move_interface_to_netns(container_iface1, ns1)
        move_interface_to_netns(container_iface2, ns2)
        move_interface_to_netns(container_iface3, ns3)
        call(f'sudo ip address add 203.0.113.10/24 dev {iface1}')
        call(f'sudo ip address add 192.0.2.10/24 dev {iface2}')
        call(f'sudo ip address add 198.51.100.10/24 dev {iface3}')

        for iface in [iface1, iface2, iface3]:
            call(f'sudo ip link set dev {iface} up')

        cmd_in_netns(ns1, f'ip link set {container_iface1} name eth0')
        cmd_in_netns(ns2, f'ip link set {container_iface2} name eth0')
        cmd_in_netns(ns3, f'ip link set {container_iface3} name eth0')
        cmd_in_netns(ns1, 'ip address add 203.0.113.1/24 dev eth0')
        cmd_in_netns(ns2, 'ip address add 192.0.2.1/24 dev eth0')
        cmd_in_netns(ns3, 'ip address add 198.51.100.1/24 dev eth0')
        cmd_in_netns(ns1, 'ip link set dev eth0 up')
        cmd_in_netns(ns2, 'ip link set dev eth0 up')
        cmd_in_netns(ns3, 'ip link set dev eth0 up')

        # Set load-balancing configuration
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'failure-count', '2'])
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'nexthop', '203.0.113.1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface1, 'success-count', '1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'failure-count', '2'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'nexthop', '192.0.2.1'])
        self.cli_set(base_path + ['wan', 'interface-health', iface2, 'success-count', '1'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'inbound-interface', iface3])
        self.cli_set(base_path + ['wan', 'rule', '10', 'source', 'address', '198.51.100.0/24'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', iface1])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', iface2, 'weight', '10'])

        # commit changes
        self.cli_commit()

        time.sleep(5)

        # Check mangle chains
        tmp = cmd(f'sudo nft -s list chain ip vyos_wanloadbalance wlb_mangle_isp_{iface1}')
        self.assertEqual(tmp, mangle_isp1)

        tmp = cmd(f'sudo nft -s list chain ip vyos_wanloadbalance wlb_mangle_isp_{iface2}')
        self.assertEqual(tmp, mangle_isp2)

        tmp = cmd('sudo nft -s list chain ip vyos_wanloadbalance wlb_mangle_prerouting')
        self.assertEqual(tmp, mangle_prerouting)

        # Check nat chains
        tmp = cmd('sudo nft -s list chain ip vyos_wanloadbalance wlb_nat_postrouting')
        self.assertEqual(tmp, nat_wanloadbalance)

        # Delete veth interfaces and netns
        for iface in [iface1, iface2, iface3]:
            call(f'sudo ip link del dev {iface}')

        delete_netns(ns1)
        delete_netns(ns2)
        delete_netns(ns3)

    def test_criteria_failover_hook(self):
        isp1_iface = 'eth0'
        isp2_iface = 'eth1'
        lan_iface = 'eth2'

        hook_path = '/tmp/wlb_hook.sh'
        hook_output_path = '/tmp/wlb_hook_output'
        hook_script = f"""
#!/bin/sh

ifname=$WLB_INTERFACE_NAME
state=$WLB_INTERFACE_STATE

echo "$ifname - $state" > {hook_output_path}
"""

        write_file(hook_path, hook_script)
        chmod_755(hook_path)

        self.cli_set(['interfaces', 'ethernet', isp1_iface, 'address', '203.0.113.2/30'])
        self.cli_set(['interfaces', 'ethernet', isp2_iface, 'address', '192.0.2.2/30'])
        self.cli_set(['interfaces', 'ethernet', lan_iface, 'address', '198.51.100.2/30'])

        self.cli_set(base_path + ['wan', 'hook', hook_path])
        self.cli_set(base_path + ['wan', 'interface-health', isp1_iface, 'failure-count', '1'])
        self.cli_set(base_path + ['wan', 'interface-health', isp1_iface, 'nexthop', '203.0.113.2'])
        self.cli_set(base_path + ['wan', 'interface-health', isp1_iface, 'success-count', '1'])
        self.cli_set(base_path + ['wan', 'interface-health', isp2_iface, 'failure-count', '1'])
        self.cli_set(base_path + ['wan', 'interface-health', isp2_iface, 'nexthop', '192.0.2.2'])
        self.cli_set(base_path + ['wan', 'interface-health', isp2_iface, 'success-count', '1'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'failover'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'inbound-interface', lan_iface])
        self.cli_set(base_path + ['wan', 'rule', '10', 'protocol', 'udp'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'source', 'address', '198.51.100.0/24'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'source', 'port', '53'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'destination', 'address', '192.0.2.0/24'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'destination', 'port', '53'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', isp1_iface])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', isp1_iface, 'weight', '10'])
        self.cli_set(base_path + ['wan', 'rule', '10', 'interface', isp2_iface])

        # commit changes
        self.cli_commit()

        time.sleep(5)

        # Verify isp1 + criteria

        nftables_search = [
            [f'iifname "{lan_iface}"', 'ip saddr 198.51.100.0/24', 'udp sport 53', 'ip daddr 192.0.2.0/24', 'udp dport 53', f'jump wlb_mangle_isp_{isp1_iface}']
        ]

        self.verify_nftables_chain(nftables_search, 'ip vyos_wanloadbalance', 'wlb_mangle_prerouting')

        # Trigger failure on isp1 health check

        self.cli_delete(['interfaces', 'ethernet', isp1_iface, 'address', '203.0.113.2/30'])
        self.cli_commit()

        time.sleep(10)

        # Verify failover to isp2

        nftables_search = [
            [f'iifname "{lan_iface}"', f'jump wlb_mangle_isp_{isp2_iface}']
        ]

        self.verify_nftables_chain(nftables_search, 'ip vyos_wanloadbalance', 'wlb_mangle_prerouting')

        # Verify hook output

        self.assertTrue(os.path.exists(hook_output_path))

        with open(hook_output_path, 'r') as f:
            self.assertIn('eth0 - FAILED', f.read())

if __name__ == '__main__':
    unittest.main(verbosity=2)
