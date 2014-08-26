# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test forms."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import json
import random

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.validators import validate_email
from maasserver.clusterrpc.power_parameters import get_power_type_choices
from maasserver.enum import (
    NODE_STATUS,
    NODEGROUP_STATUS,
    NODEGROUPINTERFACE_MANAGEMENT,
    )
from maasserver.forms import (
    AdminNodeForm,
    AdminNodeWithMACAddressesForm,
    BLANK_CHOICE,
    CommissioningForm,
    CommissioningScriptForm,
    ERROR_MESSAGE_STATIC_IPS_OUTSIDE_RANGE,
    ERROR_MESSAGE_STATIC_RANGE_IN_USE,
    get_node_create_form,
    get_node_edit_form,
    initialize_node_group,
    INTERFACES_VALIDATION_ERROR_MESSAGE,
    list_all_usable_architectures,
    MACAddressForm,
    MAX_MESSAGES,
    merge_error_messages,
    NO_ARCHITECTURES_AVAILABLE,
    NodeForm,
    NodeGroupDefineForm,
    NodeGroupEdit,
    NodeGroupInterfaceForeignDHCPForm,
    NodeGroupInterfaceForm,
    NodeWithMACAddressesForm,
    pick_default_architecture,
    remove_None_values,
    UnconstrainedMultipleChoiceField,
    validate_new_static_ip_ranges,
    validate_nonoverlapping_networks,
    ValidatorMultipleChoiceField,
    )
from maasserver.models import (
    MACAddress,
    Network,
    Node,
    NodeGroup,
    NodeGroupInterface,
    )
from maasserver.models.network import get_name_and_vlan_from_cluster_interface
from maasserver.models.staticipaddress import StaticIPAddress
from maasserver.testing.architecture import (
    make_usable_architecture,
    patch_usable_architectures,
    )
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.osystems import (
    make_osystem_with_releases,
    make_usable_osystem,
    patch_usable_osystems,
    )
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.forms import compose_invalid_choice_text
from maastesting.matchers import MockCalledOnceWith
from metadataserver.models import CommissioningScript
from netaddr import IPNetwork
from provisioningserver import tasks
from provisioningserver.utils.enum import map_enum
from testtools import TestCase
from testtools.matchers import (
    AllMatch,
    Contains,
    Equals,
    MatchesAll,
    MatchesRegex,
    MatchesStructure,
    StartsWith,
    )


class TestHelpers(MAASServerTestCase):

    def make_usable_boot_images(self, nodegroup=None, osystem=None,
                                arch=None, subarchitecture=None, release=None):
        """Create a set of boot images, so the architecture becomes "usable".

        This will make the images' architecture show up in the list of usable
        architecture.

        Nothing is returned.
        """
        if nodegroup is None:
            nodegroup = factory.make_node_group()
        if osystem is None:
            osystem = factory.make_name('os')
        if arch is None:
            arch = factory.make_name('arch')
        if subarchitecture is None:
            subarchitecture = factory.make_name('subarch')
        if release is None:
            release = factory.make_name('release')
        for purpose in ['install', 'commissioning']:
            factory.make_boot_image(
                nodegroup=nodegroup, osystem=osystem, architecture=arch,
                subarchitecture=subarchitecture, release=release,
                purpose=purpose)

    def test_initialize_node_group_leaves_nodegroup_reference_intact(self):
        preselected_nodegroup = factory.make_node_group()
        node = factory.make_node(nodegroup=preselected_nodegroup)
        initialize_node_group(node)
        self.assertEqual(preselected_nodegroup, node.nodegroup)

    def test_initialize_node_group_initializes_nodegroup_to_form_value(self):
        node = Node(
            NODE_STATUS.NEW, architecture=make_usable_architecture(self))
        nodegroup = factory.make_node_group()
        initialize_node_group(node, nodegroup)
        self.assertEqual(nodegroup, node.nodegroup)

    def test_initialize_node_group_defaults_to_master(self):
        node = Node(
            NODE_STATUS.NEW,
            architecture=make_usable_architecture(self))
        initialize_node_group(node)
        self.assertEqual(NodeGroup.objects.ensure_master(), node.nodegroup)

    def test_list_all_usable_architectures_combines_nodegroups(self):
        arches = [
            (factory.make_name('arch'), factory.make_name('subarch'))
            for _ in range(3)]
        for arch, subarch in arches:
            self.make_usable_boot_images(arch=arch, subarchitecture=subarch)
        expected = [
            "%s/%s" % (arch, subarch) for arch, subarch in arches]
        self.assertItemsEqual(expected, list_all_usable_architectures())

    def test_list_all_usable_architectures_sorts_output(self):
        arches = [
            (factory.make_name('arch'), factory.make_name('subarch'))
            for _ in range(3)]
        for arch, subarch in arches:
            self.make_usable_boot_images(arch=arch, subarchitecture=subarch)
        expected = [
            "%s/%s" % (arch, subarch) for arch, subarch in arches]
        self.assertEqual(sorted(expected), list_all_usable_architectures())

    def test_list_all_usable_architectures_returns_no_duplicates(self):
        arch = factory.make_name('arch')
        subarch = factory.make_name('subarch')
        self.make_usable_boot_images(arch=arch, subarchitecture=subarch)
        self.make_usable_boot_images(arch=arch, subarchitecture=subarch)
        self.assertEqual(
            ["%s/%s" % (arch, subarch)], list_all_usable_architectures())

    def test_pick_default_architecture_returns_empty_if_no_options(self):
        self.assertEqual('', pick_default_architecture([]))

    def test_pick_default_architecture_prefers_i386_generic_if_usable(self):
        self.assertEqual(
            'i386/generic',
            pick_default_architecture(
                ['amd64/generic', 'i386/generic', 'mips/generic']))

    def test_pick_default_architecture_falls_back_to_first_option(self):
        arches = [factory.make_name('arch') for _ in range(5)]
        self.assertEqual(arches[0], pick_default_architecture(arches))

    def test_remove_None_values_removes_None_values_in_dict(self):
        random_input = factory.make_string()
        self.assertEqual(
            {random_input: random_input},
            remove_None_values({
                random_input: random_input,
                factory.make_string(): None,
                }))

    def test_remove_None_values_leaves_empty_dict_untouched(self):
        self.assertEqual({}, remove_None_values({}))

    def test_get_node_edit_form_returns_NodeForm_if_non_admin(self):
        user = factory.make_user()
        self.assertEqual(NodeForm, get_node_edit_form(user))

    def test_get_node_edit_form_returns_APIAdminNodeEdit_if_admin(self):
        admin = factory.make_admin()
        self.assertEqual(AdminNodeForm, get_node_edit_form(admin))

    def test_get_node_create_form_if_non_admin(self):
        user = factory.make_user()
        self.assertEqual(
            NodeWithMACAddressesForm, get_node_create_form(user))

    def test_get_node_create_form_if_admin(self):
        admin = factory.make_admin()
        self.assertEqual(
            AdminNodeWithMACAddressesForm, get_node_create_form(admin))


class TestNodeForm(MAASServerTestCase):

    def test_contains_limited_set_of_fields(self):
        form = NodeForm()

        self.assertEqual(
            [
                'hostname',
                'architecture',
                'osystem',
                'distro_series',
                'license_key',
                'disable_ipv4',
                'nodegroup',
            ], list(form.fields))

    def test_changes_node(self):
        node = factory.make_node()
        hostname = factory.make_string()
        patch_usable_architectures(self, [node.architecture])

        form = NodeForm(
            data={
                'hostname': hostname,
                'architecture': make_usable_architecture(self),
                },
            instance=node)
        form.save()

        self.assertEqual(hostname, node.hostname)

    def test_accepts_usable_architecture(self):
        arch = make_usable_architecture(self)
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': arch,
            })
        self.assertTrue(form.is_valid(), form._errors)

    def test_rejects_unusable_architecture(self):
        patch_usable_architectures(self)
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': factory.make_name('arch'),
            })
        self.assertFalse(form.is_valid())
        self.assertItemsEqual(['architecture'], form._errors.keys())

    def test_starts_with_default_architecture(self):
        arches = sorted([factory.make_name('arch') for _ in range(5)])
        patch_usable_architectures(self, arches)
        form = NodeForm()
        self.assertEqual(
            pick_default_architecture(arches),
            form.fields['architecture'].initial)

    def test_adds_blank_default_when_no_arches_available(self):
        patch_usable_architectures(self, [])
        form = NodeForm()
        self.assertEqual(
            [BLANK_CHOICE],
            form.fields['architecture'].choices)

    def test_adds_error_when_no_arches_available(self):
        patch_usable_architectures(self, [])
        form = NodeForm()
        self.assertFalse(form.is_valid())
        self.assertEqual(
            [NO_ARCHITECTURES_AVAILABLE],
            form.errors['architecture'])

    def test_accepts_osystem(self):
        osystem = make_usable_osystem(self)
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            })
        self.assertTrue(form.is_valid(), form._errors)

    def test_rejects_invalid_osystem(self):
        patch_usable_osystems(self)
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': factory.make_name('os'),
            })
        self.assertFalse(form.is_valid())
        self.assertItemsEqual(['osystem'], form._errors.keys())

    def test_starts_with_default_osystem(self):
        osystems = [make_osystem_with_releases(self) for _ in range(5)]
        patch_usable_osystems(self, osystems)
        form = NodeForm()
        self.assertEqual(
            '',
            form.fields['osystem'].initial)

    def test_accepts_osystem_distro_series(self):
        osystem = make_usable_osystem(self)
        release = osystem.get_default_release()
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            'distro_series': '%s/%s' % (osystem.name, release),
            })
        self.assertTrue(form.is_valid(), form._errors)

    def test_rejects_invalid_osystem_distro_series(self):
        osystem = make_usable_osystem(self)
        release = factory.make_name('release')
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            'distro_series': '%s/%s' % (osystem.name, release),
            })
        self.assertFalse(form.is_valid())
        self.assertItemsEqual(['distro_series'], form._errors.keys())

    def test_starts_with_default_distro_series(self):
        osystems = [make_osystem_with_releases(self) for _ in range(5)]
        patch_usable_osystems(self, osystems)
        form = NodeForm()
        self.assertEqual(
            '',
            form.fields['distro_series'].initial)

    def test_rejects_mismatch_osystem_distro_series(self):
        osystem = make_usable_osystem(self)
        release = osystem.get_default_release()
        invalid = factory.make_name('invalid_os')
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            'distro_series': '%s/%s' % (invalid, release),
            })
        self.assertFalse(form.is_valid())
        self.assertItemsEqual(['distro_series'], form._errors.keys())

    def test_rejects_missing_license_key(self):
        osystem = make_usable_osystem(self)
        release = osystem.get_default_release()
        self.patch(osystem, 'requires_license_key').return_value = True
        mock_validate = self.patch(osystem, 'validate_license_key')
        mock_validate.return_value = True
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            'distro_series': '%s/%s*' % (osystem.name, release),
            })
        self.assertFalse(form.is_valid())
        self.assertItemsEqual(['license_key'], form._errors.keys())

    def test_calls_validate_license_key(self):
        osystem = make_usable_osystem(self)
        release = osystem.get_default_release()
        self.patch(osystem, 'requires_license_key').return_value = True
        mock_validate = self.patch(osystem, 'validate_license_key')
        mock_validate.return_value = True
        form = NodeForm(data={
            'hostname': factory.make_name('host'),
            'architecture': make_usable_architecture(self),
            'osystem': osystem.name,
            'distro_series': '%s/%s*' % (osystem.name, release),
            'license_key': factory.make_string(),
            })
        self.assertTrue(form.is_valid())
        mock_validate.assert_called_once()

    def test_rejects_duplicate_fqdn_with_unmanaged_dns_on_one_nodegroup(self):
        # If a host with a given hostname exists on a managed nodegroup,
        # new nodes on unmanaged nodegroups with hostnames that match
        # that FQDN will be rejected.
        nodegroup = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS)
        node = factory.make_node(
            hostname=factory.make_name("hostname"), nodegroup=nodegroup)
        other_nodegroup = factory.make_node_group()
        form = NodeForm(data={
            'nodegroup': other_nodegroup,
            'hostname': node.fqdn,
            'architecture': make_usable_architecture(self),
        })
        form.instance.nodegroup = other_nodegroup
        self.assertFalse(form.is_valid())

    def test_rejects_duplicate_fqdn_on_same_nodegroup(self):
        # If a node with a given FQDN exists on a managed nodegroup, new
        # nodes on that nodegroup with duplicate FQDNs will be rejected.
        nodegroup = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS)
        node = factory.make_node(
            hostname=factory.make_name("hostname"), nodegroup=nodegroup)
        form = NodeForm(data={
            'nodegroup': nodegroup,
            'hostname': node.fqdn,
            'architecture': make_usable_architecture(self),
        })
        form.instance.nodegroup = nodegroup
        self.assertFalse(form.is_valid())


class TestAdminNodeForm(MAASServerTestCase):

    def test_AdminNodeForm_contains_limited_set_of_fields(self):
        node = factory.make_node()
        form = AdminNodeForm(instance=node)

        self.assertEqual(
            [
                'hostname',
                'architecture',
                'osystem',
                'distro_series',
                'license_key',
                'disable_ipv4',
                'power_type',
                'power_parameters',
                'cpu_count',
                'memory',
                'storage',
                'zone',
            ],
            list(form.fields))

    def test_AdminNodeForm_initialises_zone(self):
        # The zone field uses "to_field_name", so that it can refer to a zone
        # by name instead of by ID.  A bug in Django breaks initialisation
        # from an instance: the field tries to initialise the field using a
        # zone's ID instead of its name, and ends up reverting to the default.
        # The code must work around this bug.
        zone = factory.make_zone()
        node = factory.make_node(zone=zone)
        # We'll create a form that makes a change, but not to the zone.
        data = {'hostname': factory.make_name('host')}
        form = AdminNodeForm(instance=node, data=data)
        # The Django bug would stop the initial field value from being set,
        # but the workaround ensures that it is initialised.
        self.assertEqual(zone.name, form.initial['zone'])

    def test_AdminNodeForm_changes_node(self):
        node = factory.make_node()
        zone = factory.make_zone()
        hostname = factory.make_string()
        power_type = factory.pick_power_type()
        form = AdminNodeForm(
            data={
                'hostname': hostname,
                'power_type': power_type,
                'architecture': make_usable_architecture(self),
                'zone': zone.name,
            },
            instance=node)
        form.save()

        node = reload_object(node)
        self.assertEqual(
            (node.hostname, node.power_type, node.zone),
            (hostname, power_type, zone))

    def test_AdminNodeForm_populates_power_type_choices(self):
        form = AdminNodeForm()
        self.assertEqual(
            [''] + [choice[0] for choice in get_power_type_choices()],
            [choice[0] for choice in form.fields['power_type'].choices])

    def test_AdminNodeForm_populates_power_type_initial(self):
        node = factory.make_node()
        form = AdminNodeForm(instance=node)
        self.assertEqual(node.power_type, form.fields['power_type'].initial)

    def test_AdminNodeForm_changes_node_with_skip_check(self):
        node = factory.make_node()
        hostname = factory.make_string()
        power_type = factory.pick_power_type()
        power_parameters_field = factory.make_string()
        arch = make_usable_architecture(self)
        form = AdminNodeForm(
            data={
                'hostname': hostname,
                'architecture': arch,
                'power_type': power_type,
                'power_parameters_field': power_parameters_field,
                'power_parameters_skip_check': True,
                },
            instance=node)
        form.save()

        self.assertEqual(
            (hostname, power_type, {'field': power_parameters_field}),
            (node.hostname, node.power_type, node.power_parameters))

    def test_AdminForm_does_not_permit_nodegroup_change(self):
        # We had to make Node.nodegroup editable to get Django to
        # validate it as non-blankable, but that doesn't mean that we
        # actually want to allow people to edit it through API or UI.
        old_nodegroup = factory.make_node_group()
        node = factory.make_node(
            nodegroup=old_nodegroup,
            architecture=make_usable_architecture(self))
        new_nodegroup = factory.make_node_group()
        AdminNodeForm(data={'nodegroup': new_nodegroup}, instance=node).save()
        # The form saved without error, but the nodegroup change was ignored.
        self.assertEqual(old_nodegroup, node.nodegroup)


class TestMergeErrorMessages(MAASServerTestCase):

    def test_merge_error_messages_returns_summary_message(self):
        summary = factory.make_name('summary')
        errors = [factory.make_name('error') for _ in range(2)]
        result = merge_error_messages(summary, errors, 5)
        self.assertEqual(
            "%s (%s)" % (summary, ' \u2014 '.join(errors)), result)

    def test_merge_error_messages_includes_limited_number_of_msgs(self):
        summary = factory.make_name('summary')
        errors = [
            factory.make_name('error')
            for _ in range(MAX_MESSAGES + 2)]
        result = merge_error_messages(summary, errors)
        self.assertEqual(
            "%s (%s and 2 more errors)" % (
                summary, ' \u2014 '.join(errors[:MAX_MESSAGES])),
            result)

    def test_merge_error_messages_with_one_more_error(self):
        summary = factory.make_name('summary')
        errors = [
            factory.make_name('error')
            for _ in range(MAX_MESSAGES + 1)]
        result = merge_error_messages(summary, errors)
        self.assertEqual(
            "%s (%s and 1 more error)" % (
                summary, ' \u2014 '.join(errors[:MAX_MESSAGES])),
            result)


class TestMACAddressForm(MAASServerTestCase):

    def test_MACAddressForm_creates_mac_address(self):
        node = factory.make_node()
        mac = factory.getRandomMACAddress()
        form = MACAddressForm(node=node, data={'mac_address': mac})
        form.save()
        self.assertTrue(
            MACAddress.objects.filter(node=node, mac_address=mac).exists())

    def test_saves_to_db_by_default(self):
        node = factory.make_node()
        mac = factory.getRandomMACAddress()
        form = MACAddressForm(node=node, data={'mac_address': mac})
        form.save()
        self.assertEqual(
            mac, MACAddress.objects.get(mac_address=mac).mac_address)

    def test_does_not_save_to_db_if_commit_is_False(self):
        node = factory.make_node()
        mac = factory.getRandomMACAddress()
        form = MACAddressForm(node=node, data={'mac_address': mac})
        form.save(commit=False)
        self.assertItemsEqual([], MACAddress.objects.filter(mac_address=mac))

    def test_MACAddressForm_displays_error_message_if_mac_already_used(self):
        mac = factory.getRandomMACAddress()
        node = factory.make_mac_address(address=mac)
        node = factory.make_node()
        form = MACAddressForm(node=node, data={'mac_address': mac})
        self.assertFalse(form.is_valid())
        self.assertEquals(
            {'mac_address': ['This MAC address is already registered.']},
            form._errors)
        self.assertFalse(
            MACAddress.objects.filter(node=node, mac_address=mac).exists())


nullable_fields = [
    'subnet_mask', 'broadcast_ip', 'router_ip', 'ip_range_low',
    'ip_range_high', 'static_ip_range_low', 'static_ip_range_high',
    ]


def make_ngi_instance(nodegroup=None):
    """Create a `NodeGroupInterface` with nothing set but `nodegroup`.

    This is used by tests to instantiate the cluster interface form for
    a given cluster.  We create an initial cluster interface object just
    to tell it which cluster that is.
    """
    if nodegroup is None:
        nodegroup = factory.make_node_group()
    return NodeGroupInterface(nodegroup=nodegroup)


class TestNodeGroupInterfaceForm(MAASServerTestCase):

    def test__validates_parameters(self):
        form = NodeGroupInterfaceForm(
            data={'ip': factory.make_string()},
            instance=make_ngi_instance())
        self.assertFalse(form.is_valid())
        self.assertEquals(
            {'ip': ['Enter a valid IPv4 or IPv6 address.']}, form._errors)

    def test__can_save_fields_being_None(self):
        int_settings = factory.get_interface_fields()
        int_settings['management'] = NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED
        for field_name in nullable_fields:
            del int_settings[field_name]
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface = form.save()
        field_values = [
            getattr(interface, field_name) for field_name in nullable_fields]
        self.assertThat(field_values, AllMatch(Equals('')))

    def test__uses_name_if_given(self):
        name = factory.make_name('explicit-name')
        int_settings = factory.get_interface_fields()
        int_settings['name'] = name
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface = form.save()
        self.assertEqual(name, interface.name)

    def test__lets_name_default_to_network_interface_name(self):
        int_settings = factory.get_interface_fields()
        int_settings['interface'] = factory.make_name('ether')
        del int_settings['name']
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface = form.save()
        self.assertEqual(int_settings['interface'], interface.name)

    def test__escapes_interface_name(self):
        int_settings = factory.get_interface_fields()
        int_settings['interface'] = 'eth1+1'
        del int_settings['name']
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface = form.save()
        self.assertEqual('eth1--1', interface.name)

    def test__defaults_to_unique_name_if_no_name_or_interface_given(self):
        int_settings = factory.get_interface_fields(
            management=NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED)
        del int_settings['name']
        del int_settings['interface']
        form1 = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface1 = form1.save()
        form2 = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        interface2 = form2.save()
        self.assertNotIn(interface1.name, [None, ''])
        self.assertNotIn(interface2.name, [None, ''])
        self.assertNotEqual(interface1.name, interface2.name)

    def test__disambiguates_default_name(self):
        cluster = factory.make_node_group()
        existing_interface = factory.make_node_group_interface(cluster)
        int_settings = factory.get_interface_fields()
        del int_settings['name']
        int_settings['interface'] = existing_interface.name
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance(cluster))
        interface = form.save()
        self.assertThat(interface.name, StartsWith(int_settings['interface']))
        self.assertNotEqual(int_settings['interface'], interface.name)

    def test_validates_new_static_ip_ranges(self):
        network = IPNetwork("10.1.0.0/24")
        nodegroup = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS,
            network=network)
        [interface] = nodegroup.get_managed_interfaces()
        StaticIPAddress.objects.allocate_new(
            interface.static_ip_range_low, interface.static_ip_range_high)
        form = NodeGroupInterfaceForm(
            data={'static_ip_range_low': '', 'static_ip_range_high': ''},
            instance=interface)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            [ERROR_MESSAGE_STATIC_RANGE_IN_USE],
            form._errors['static_ip_range_low'])
        self.assertEqual(
            [ERROR_MESSAGE_STATIC_RANGE_IN_USE],
            form._errors['static_ip_range_high'])

    def test_calls_get_duplicate_fqdns_when_appropriate(self):
        # Check for duplicate FQDNs if the NodeGroupInterface has a
        # NodeGroup and is managing DNS.
        int_settings = factory.get_interface_fields(
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS)
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        mock = self.patch(form, "get_duplicate_fqdns")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertThat(mock, MockCalledOnceWith())

    def test_reports_error_if_fqdns_duplicated(self):
        int_settings = factory.get_interface_fields(
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS)
        form = NodeGroupInterfaceForm(
            data=int_settings, instance=make_ngi_instance())
        mock = self.patch(form, "get_duplicate_fqdns")
        hostnames = [
            factory.make_hostname("duplicate") for _ in range(0, 3)]
        mock.return_value = hostnames
        self.assertFalse(form.is_valid())
        message = "Enabling DNS management creates duplicate FQDN(s): %s." % (
            ", ".join(set(hostnames)))
        self.assertEqual(
            {'management': [message]},
            form.errors)

    def test_identifies_duplicate_fqdns_in_nodegroup(self):
        # Don't allow DNS management to be enabled when it would
        # cause more than one node on the nodegroup to have the
        # same FQDN.
        nodegroup = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        base_hostname = factory.make_hostname("host")
        full_hostnames = [
            "%s.%s" % (base_hostname, factory.make_hostname("domain"))
            for _ in range(0, 2)]
        for hostname in full_hostnames:
            factory.make_node(hostname=hostname, nodegroup=nodegroup)
        [interface] = nodegroup.get_managed_interfaces()
        data = {"management": NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS}
        form = NodeGroupInterfaceForm(data=data, instance=interface)
        duplicates = form.get_duplicate_fqdns()
        expected_duplicates = set(["%s.%s" % (base_hostname, nodegroup.name)])
        self.assertEqual(expected_duplicates, duplicates)

    def test_identifies_duplicate_fqdns_across_nodegroups(self):
        # Don't allow DNS management to be enabled when it would
        # cause a node in this nodegroup to have the same FQDN
        # as a node in another nodegroup.

        conflicting_domain = factory.make_hostname("conflicting-domain")
        nodegroup_a = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP,
            name=conflicting_domain)
        conflicting_hostname = factory.make_hostname("conflicting-hostname")
        factory.make_node(
            hostname="%s.%s" % (conflicting_hostname, conflicting_domain),
            nodegroup=nodegroup_a)

        nodegroup_b = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP,
            name=conflicting_domain)
        factory.make_node(
            hostname="%s.%s" % (
                conflicting_hostname, factory.make_hostname("other-domain")),
            nodegroup=nodegroup_b)

        [interface] = nodegroup_b.get_managed_interfaces()
        data = {"management": NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS}
        form = NodeGroupInterfaceForm(data=data, instance=interface)
        duplicates = form.get_duplicate_fqdns()
        expected_duplicates = set(
            ["%s.%s" % (conflicting_hostname, conflicting_domain)])
        self.assertEqual(expected_duplicates, duplicates)


class TestNodeGroupInterfaceFormNetworkCreation(MAASServerTestCase):
    """Tests for when NodeGroupInterfaceForm creates a Network."""

    def test_creates_network_name(self):
        int_settings = factory.get_interface_fields()
        int_settings['interface'] = 'eth0:1'
        interface = make_ngi_instance()
        form = NodeGroupInterfaceForm(data=int_settings, instance=interface)
        form.save()
        [network] = Network.objects.all()
        expected, _ = get_name_and_vlan_from_cluster_interface(interface)
        self.assertEqual(expected, network.name)

    def test_sets_vlan_tag(self):
        int_settings = factory.get_interface_fields()
        vlan_tag = random.randint(1, 10)
        int_settings['interface'] = 'eth0.%s' % vlan_tag
        interface = make_ngi_instance()
        form = NodeGroupInterfaceForm(data=int_settings, instance=interface)
        form.save()
        [network] = Network.objects.all()
        self.assertEqual(vlan_tag, network.vlan_tag)

    def test_vlan_tag_is_None_if_no_vlan(self):
        int_settings = factory.get_interface_fields()
        int_settings['interface'] = 'eth0:1'
        interface = make_ngi_instance()
        form = NodeGroupInterfaceForm(data=int_settings, instance=interface)
        form.save()
        [network] = Network.objects.all()
        self.assertIs(None, network.vlan_tag)

    def test_sets_network_values(self):
        int_settings = factory.get_interface_fields()
        interface = make_ngi_instance()
        form = NodeGroupInterfaceForm(data=int_settings, instance=interface)
        form.save()
        [network] = Network.objects.all()
        expected_net_address = unicode(interface.network.network)
        expected_netmask = unicode(interface.network.netmask)
        self.assertThat(
            network, MatchesStructure.byEquality(
                ip=expected_net_address,
                netmask=expected_netmask))

    def test_does_not_create_new_network_if_already_exists(self):
        int_settings = factory.get_interface_fields()
        interface = make_ngi_instance()
        form = NodeGroupInterfaceForm(data=int_settings, instance=interface)
        # The easiest way to pre-create the same network is just to save
        # the form twice.
        form.save()
        [existing_network] = Network.objects.all()
        form.save()
        self.assertItemsEqual([existing_network], Network.objects.all())

    def test_creates_many_unique_networks(self):
        names = ('eth0', 'eth0:1', 'eth0.1', 'eth0:1.2')
        for name in names:
            int_settings = factory.get_interface_fields()
            int_settings['interface'] = name
            interface = make_ngi_instance()
            form = NodeGroupInterfaceForm(
                data=int_settings, instance=interface)
            form.save()

        self.assertEqual(len(names), len(Network.objects.all()))


class TestValidateNewStaticIPRanges(MAASServerTestCase):
    """Tests for `validate_new_static_ip_ranges`()."""

    def make_interface(self):
        network = IPNetwork("10.1.0.0/24")
        nodegroup = factory.make_node_group(
            status=NODEGROUP_STATUS.ACCEPTED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS,
            network=network)
        [interface] = nodegroup.get_managed_interfaces()
        interface.ip_range_low = '10.1.0.1'
        interface.ip_range_high = '10.1.0.10'
        interface.static_ip_range_low = '10.1.0.50'
        interface.static_ip_range_high = '10.1.0.60'
        interface.save()
        return interface

    def test_raises_error_when_allocated_ips_fall_outside_new_range(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new('10.1.0.56', '10.1.0.60')
        error = self.assertRaises(
            ValidationError,
            validate_new_static_ip_ranges,
            instance=interface,
            static_ip_range_low='10.1.0.50',
            static_ip_range_high='10.1.0.55')
        self.assertEqual(
            ERROR_MESSAGE_STATIC_IPS_OUTSIDE_RANGE,
            error.message)

    def test_removing_static_range_raises_error_if_ips_allocated(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new('10.1.0.56', '10.1.0.60')
        error = self.assertRaises(
            ValidationError,
            validate_new_static_ip_ranges,
            instance=interface,
            static_ip_range_low='',
            static_ip_range_high='')
        self.assertEqual(
            ERROR_MESSAGE_STATIC_RANGE_IN_USE,
            error.message)

    def test_allows_range_expansion(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new('10.1.0.56', '10.1.0.60')
        is_valid = validate_new_static_ip_ranges(
            interface, static_ip_range_low='10.1.0.40',
            static_ip_range_high='10.1.0.100')
        self.assertTrue(is_valid)

    def test_allows_allocated_ip_as_upper_bound(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new('10.1.0.55', '10.1.0.55')
        is_valid = validate_new_static_ip_ranges(
            interface,
            static_ip_range_low=interface.static_ip_range_low,
            static_ip_range_high='10.1.0.55')
        self.assertTrue(is_valid)

    def test_allows_allocated_ip_as_lower_bound(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new('10.1.0.55', '10.1.0.55')
        is_valid = validate_new_static_ip_ranges(
            interface, static_ip_range_low='10.1.0.55',
            static_ip_range_high=interface.static_ip_range_high)
        self.assertTrue(is_valid)

    def test_ignores_unmanaged_interfaces(self):
        interface = self.make_interface()
        interface.management = NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED
        interface.save()
        StaticIPAddress.objects.allocate_new(
            interface.static_ip_range_low, interface.static_ip_range_high)
        is_valid = validate_new_static_ip_ranges(
            interface, static_ip_range_low='10.1.0.57',
            static_ip_range_high='10.1.0.58')
        self.assertTrue(is_valid)

    def test_ignores_interfaces_with_no_static_range(self):
        interface = self.make_interface()
        interface.static_ip_range_low = None
        interface.static_ip_range_high = None
        interface.save()
        StaticIPAddress.objects.allocate_new('10.1.0.56', '10.1.0.60')
        is_valid = validate_new_static_ip_ranges(
            interface, static_ip_range_low='10.1.0.57',
            static_ip_range_high='10.1.0.58')
        self.assertTrue(is_valid)

    def test_ignores_unchanged_static_range(self):
        interface = self.make_interface()
        StaticIPAddress.objects.allocate_new(
            interface.static_ip_range_low, interface.static_ip_range_high)
        is_valid = validate_new_static_ip_ranges(
            interface,
            static_ip_range_low=interface.static_ip_range_low,
            static_ip_range_high=interface.static_ip_range_high)
        self.assertTrue(is_valid)


class TestNodeGroupInterfaceForeignDHCPForm(MAASServerTestCase):

    def test_forms_saves_foreign_dhcp_ip(self):
        nodegroup = factory.make_node_group()
        interface = factory.make_node_group_interface(nodegroup)
        foreign_dhcp_ip = factory.getRandomIPAddress()
        form = NodeGroupInterfaceForeignDHCPForm(
            data={'foreign_dhcp_ip': foreign_dhcp_ip},
            instance=interface)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(
            foreign_dhcp_ip, reload_object(interface).foreign_dhcp_ip)

    def test_forms_validates_foreign_dhcp_ip(self):
        nodegroup = factory.make_node_group()
        interface = factory.make_node_group_interface(nodegroup)
        form = NodeGroupInterfaceForeignDHCPForm(
            data={'foreign_dhcp_ip': 'invalid-ip'}, instance=interface)
        self.assertFalse(form.is_valid())

    def test_report_foreign_dhcp_does_not_trigger_update_signal(self):
        self.patch(settings, "DHCP_CONNECT", False)
        nodegroup = factory.make_node_group(status=NODEGROUP_STATUS.ACCEPTED)
        interface = factory.make_node_group_interface(
            nodegroup, management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)

        self.patch(settings, "DHCP_CONNECT", True)
        self.patch(tasks, 'write_dhcp_config')

        foreign_dhcp_ip = factory.getRandomIPAddress()
        form = NodeGroupInterfaceForeignDHCPForm(
            data={'foreign_dhcp_ip': foreign_dhcp_ip},
            instance=interface)

        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(
            foreign_dhcp_ip, reload_object(interface).foreign_dhcp_ip)
        tasks.write_dhcp_config.apply_async.assert_has_calls([])


class TestValidateNonoverlappingNetworks(TestCase):
    """Tests for `validate_nonoverlapping_networks`."""

    def make_interface_definition(self, ip, netmask, name=None):
        """Return a minimal imitation of an interface definition."""
        if name is None:
            name = factory.make_name('itf')
        return {
            'interface': name,
            'ip': ip,
            'subnet_mask': netmask,
            'management': NODEGROUPINTERFACE_MANAGEMENT.DHCP_AND_DNS,
        }

    def test_accepts_zero_interfaces(self):
        validate_nonoverlapping_networks([])
        # Success is getting here without error.
        pass

    def test_accepts_single_interface(self):
        validate_nonoverlapping_networks(
            [self.make_interface_definition('10.1.1.1', '255.255.0.0')])
        # Success is getting here without error.
        pass

    def test_accepts_disparate_ranges(self):
        validate_nonoverlapping_networks([
            self.make_interface_definition('10.1.0.0', '255.255.0.0'),
            self.make_interface_definition('192.168.0.0', '255.255.255.0'),
            ])
        # Success is getting here without error.
        pass

    def test_accepts_near_neighbours(self):
        validate_nonoverlapping_networks([
            self.make_interface_definition('10.1.0.0', '255.255.0.0'),
            self.make_interface_definition('10.2.0.0', '255.255.0.0'),
            ])
        # Success is getting here without error.
        pass

    def test_rejects_identical_ranges(self):
        definitions = [
            self.make_interface_definition('192.168.0.0', '255.255.255.0'),
            self.make_interface_definition('192.168.0.0', '255.255.255.0'),
            ]
        error = self.assertRaises(
            ValidationError,
            validate_nonoverlapping_networks, definitions)
        error_text = error.messages[0]
        self.assertThat(
            error_text, MatchesRegex(
                "Conflicting networks on [^\\s]+ and [^\\s]+: "
                "address ranges overlap."))
        self.assertThat(
            error_text,
            MatchesAll(
                *(
                    Contains(definition['interface'])
                    for definition in definitions
                )))

    def test_rejects_nested_ranges(self):
        definitions = [
            self.make_interface_definition('192.168.0.0', '255.255.0.0'),
            self.make_interface_definition('192.168.100.0', '255.255.255.0'),
            ]
        error = self.assertRaises(
            ValidationError,
            validate_nonoverlapping_networks, definitions)
        self.assertIn("Conflicting networks", unicode(error))

    def test_detects_conflict_regardless_of_order(self):
        definitions = [
            self.make_interface_definition('192.168.100.0', '255.255.255.0'),
            self.make_interface_definition('192.168.1.0', '255.255.255.0'),
            self.make_interface_definition('192.168.64.0', '255.255.192.0'),
            ]
        error = self.assertRaises(
            ValidationError,
            validate_nonoverlapping_networks, definitions)
        self.assertThat(error.messages[0], StartsWith("Conflicting networks"))


class TestNodeGroupDefineForm(MAASServerTestCase):

    def test_creates_pending_nodegroup(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        form = NodeGroupDefineForm(data={'name': name, 'uuid': uuid})
        self.assertTrue(form.is_valid(), form._errors)
        nodegroup = form.save()
        self.assertEqual(
            (uuid, name, NODEGROUP_STATUS.PENDING, 0),
            (
                nodegroup.uuid,
                nodegroup.name,
                nodegroup.status,
                nodegroup.nodegroupinterface_set.count(),
            ))

    def test_creates_nodegroup_with_status(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        form = NodeGroupDefineForm(
            status=NODEGROUP_STATUS.ACCEPTED,
            data={'name': name, 'uuid': uuid})
        self.assertTrue(form.is_valid(), form._errors)
        nodegroup = form.save()
        self.assertEqual(NODEGROUP_STATUS.ACCEPTED, nodegroup.status)

    def test_validates_parameters(self):
        name = factory.make_name('name')
        too_long_uuid = 'test' * 30
        form = NodeGroupDefineForm(
            data={'name': name, 'uuid': too_long_uuid})
        self.assertFalse(form.is_valid())
        self.assertEquals(
            {'uuid':
                ['Ensure this value has at most 36 characters (it has 120).']},
            form._errors)

    def test_rejects_invalid_json_interfaces(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        invalid_interfaces = factory.make_name('invalid_json_interfaces')
        form = NodeGroupDefineForm(
            data={
                'name': name, 'uuid': uuid, 'interfaces': invalid_interfaces})
        self.assertFalse(form.is_valid())
        self.assertEquals(
            {'interfaces': ['Invalid json value.']},
            form._errors)

    def test_rejects_invalid_list_interfaces(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        invalid_interfaces = json.dumps('invalid interface list')
        form = NodeGroupDefineForm(
            data={
                'name': name, 'uuid': uuid, 'interfaces': invalid_interfaces})
        self.assertFalse(form.is_valid())
        self.assertEquals(
            {'interfaces': [INTERFACES_VALIDATION_ERROR_MESSAGE]},
            form._errors)

    def test_rejects_invalid_interface(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        interface = factory.get_interface_fields()
        # Make the interface invalid.
        interface['ip_range_high'] = 'invalid IP address'
        interfaces = json.dumps([interface])
        form = NodeGroupDefineForm(
            data={'name': name, 'uuid': uuid, 'interfaces': interfaces})
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Enter a valid IPv4 or IPv6 address",
            form._errors['interfaces'][0])

    def test_creates_interface_from_params(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        interface = factory.get_interface_fields()
        interfaces = json.dumps([interface])
        form = NodeGroupDefineForm(
            data={'name': name, 'uuid': uuid, 'interfaces': interfaces})
        self.assertTrue(form.is_valid(), form._errors)
        form.save()
        nodegroup = NodeGroup.objects.get(uuid=uuid)
        # Replace empty strings with None as empty strings are converted into
        # None for fields with null=True.
        expected_result = {
            key: (value if value != '' else None)
            for key, value in interface.items()
        }
        self.assertThat(
            nodegroup.nodegroupinterface_set.all()[0],
            MatchesStructure.byEquality(**expected_result))

    def test_accepts_unnamed_cluster_interface(self):
        uuid = factory.make_UUID()
        interface = factory.get_interface_fields()
        del interface['name']
        interfaces = json.dumps([interface])
        form = NodeGroupDefineForm(
            data={
                'name': factory.make_name('cluster'),
                'uuid': uuid,
                'interfaces': interfaces,
            })
        self.assertTrue(form.is_valid(), form._errors)
        cluster = form.save()
        [cluster_interface] = cluster.nodegroupinterface_set.all()
        self.assertEqual(interface['interface'], cluster_interface.name)
        self.assertEqual(interface['interface'], cluster_interface.interface)

    def test_checks_against_conflicting_managed_networks(self):
        big_network = IPNetwork('10.0.0.0/255.255.0.0')
        nested_network = IPNetwork('10.0.100.0/255.255.255.0')
        managed = NODEGROUPINTERFACE_MANAGEMENT.DHCP
        form = NodeGroupDefineForm(
            data={
                'name': factory.make_name('cluster'),
                'uuid': factory.make_UUID(),
                'interfaces': json.dumps([
                    factory.get_interface_fields(
                        network=big_network, management=managed),
                    factory.get_interface_fields(
                        network=nested_network, management=managed),
                    ]),
            })
        self.assertFalse(form.is_valid())
        self.assertNotEqual([], form._errors['interfaces'])
        self.assertThat(
            form._errors['interfaces'][0],
            StartsWith("Conflicting networks"))

    def test_ignores_conflicts_on_unmanaged_interfaces(self):
        big_network = IPNetwork('10.0.0.0/255.255.0.0')
        nested_network = IPNetwork('10.100.100.0/255.255.255.0')
        managed = NODEGROUPINTERFACE_MANAGEMENT.DHCP
        unmanaged = NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED
        form = NodeGroupDefineForm(
            data={
                'name': factory.make_name('cluster'),
                'uuid': factory.make_UUID(),
                'interfaces': json.dumps([
                    factory.get_interface_fields(
                        network=big_network, management=managed),
                    factory.get_interface_fields(
                        network=nested_network, management=unmanaged),
                    ]),
            })
        is_valid = form.is_valid()
        self.assertEqual(
            (True, None),
            (is_valid, form._errors.get('interfaces')))

    def test_creates_multiple_interfaces(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        interfaces = [
            factory.get_interface_fields(management=management)
            for management in map_enum(NODEGROUPINTERFACE_MANAGEMENT).values()
            ]
        form = NodeGroupDefineForm(
            data={
                'name': name,
                'uuid': uuid,
                'interfaces': json.dumps(interfaces),
                })
        self.assertTrue(form.is_valid(), form._errors)
        form.save()
        nodegroup = NodeGroup.objects.get(uuid=uuid)
        self.assertEqual(
            len(interfaces), nodegroup.nodegroupinterface_set.count())

    def test_populates_cluster_name_default(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        form = NodeGroupDefineForm(
            status=NODEGROUP_STATUS.ACCEPTED,
            data={'name': name, 'uuid': uuid})
        self.assertTrue(form.is_valid(), form._errors)
        nodegroup = form.save()
        self.assertIn(uuid, nodegroup.cluster_name)

    def test_populates_cluster_name(self):
        cluster_name = factory.make_name('cluster_name')
        uuid = factory.make_UUID()
        form = NodeGroupDefineForm(
            status=NODEGROUP_STATUS.ACCEPTED,
            data={'cluster_name': cluster_name, 'uuid': uuid})
        self.assertTrue(form.is_valid(), form._errors)
        nodegroup = form.save()
        self.assertEqual(cluster_name, nodegroup.cluster_name)

    def test_creates_unmanaged_interfaces(self):
        name = factory.make_name('name')
        uuid = factory.make_UUID()
        interface = factory.get_interface_fields()
        del interface['management']
        interfaces = json.dumps([interface])
        form = NodeGroupDefineForm(
            data={'name': name, 'uuid': uuid, 'interfaces': interfaces})
        self.assertTrue(form.is_valid(), form._errors)
        form.save()
        uuid_nodegroup = NodeGroup.objects.get(uuid=uuid)
        self.assertEqual(
            [NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED],
            [
                nodegroup.management for nodegroup in
                uuid_nodegroup.nodegroupinterface_set.all()
            ])


class TestNodeGroupEdit(MAASServerTestCase):

    def make_form_data(self, nodegroup):
        """Create `NodeGroupEdit` form data based on `nodegroup`."""
        return {
            'name': nodegroup.name,
            'cluster_name': nodegroup.cluster_name,
            'status': nodegroup.status,
        }

    def test_changes_name(self):
        nodegroup = factory.make_node_group(name=factory.make_name('old-name'))
        new_name = factory.make_name('new-name')
        data = self.make_form_data(nodegroup)
        data['name'] = new_name
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(new_name, reload_object(nodegroup).name)

    def test_refuses_name_change_if_dns_managed_and_nodes_in_use(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        data = self.make_form_data(nodegroup)
        data['name'] = factory.make_name('new-name')
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertFalse(form.is_valid())

    def test_accepts_unchanged_name(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        original_name = nodegroup.name
        form = NodeGroupEdit(
            instance=nodegroup, data=self.make_form_data(nodegroup))
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(original_name, reload_object(nodegroup).name)

    def test_accepts_omitted_name(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        original_name = nodegroup.name
        data = self.make_form_data(nodegroup)
        del data['name']
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(original_name, reload_object(nodegroup).name)

    def test_accepts_name_change_if_nodegroup_not_accepted(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        nodegroup.status = NODEGROUP_STATUS.PENDING
        data = self.make_form_data(nodegroup)
        data['name'] = factory.make_name('new-name')
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())

    def test_accepts_name_change_if_dns_managed_but_no_nodes_in_use(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        node.status = NODE_STATUS.READY
        node.save()
        data = self.make_form_data(nodegroup)
        data['name'] = factory.make_name('new-name')
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(data['name'], reload_object(nodegroup).name)

    def test_accepts_name_change_if_nodes_in_use_but_dns_not_managed(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        [interface] = nodegroup.get_managed_interfaces()
        interface.management = NODEGROUPINTERFACE_MANAGEMENT.DHCP
        interface.save()
        data = self.make_form_data(nodegroup)
        data['name'] = factory.make_name('new-name')
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(data['name'], reload_object(nodegroup).name)

    def test_accepts_name_change_if_nodegroup_has_no_interface(self):
        nodegroup, node = factory.make_unrenamable_nodegroup_with_node()
        NodeGroupInterface.objects.filter(nodegroup=nodegroup).delete()
        data = self.make_form_data(nodegroup)
        data['name'] = factory.make_name('new-name')
        form = NodeGroupEdit(instance=nodegroup, data=data)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertEqual(data['name'], reload_object(nodegroup).name)


class TestCommissioningFormForm(MAASServerTestCase):

    def test_commissioningform_error_msg_lists_series_choices(self):
        form = CommissioningForm()
        field = form.fields['commissioning_distro_series']
        self.assertEqual(
            compose_invalid_choice_text(
                'commissioning_distro_series', field.choices),
            field.error_messages['invalid_choice'])


class TestCommissioningScriptForm(MAASServerTestCase):

    def test_creates_commissioning_script(self):
        content = factory.make_string().encode('ascii')
        name = factory.make_name('filename')
        uploaded_file = SimpleUploadedFile(content=content, name=name)
        form = CommissioningScriptForm(files={'content': uploaded_file})
        self.assertTrue(form.is_valid(), form._errors)
        form.save()
        new_script = CommissioningScript.objects.get(name=name)
        self.assertThat(
            new_script,
            MatchesStructure.byEquality(name=name, content=content))

    def test_raises_if_duplicated_name(self):
        content = factory.make_string().encode('ascii')
        name = factory.make_name('filename')
        factory.make_commissioning_script(name=name)
        uploaded_file = SimpleUploadedFile(content=content, name=name)
        form = CommissioningScriptForm(files={'content': uploaded_file})
        self.assertEqual(
            (False, {'content': ["A script with that name already exists."]}),
            (form.is_valid(), form._errors))

    def test_rejects_whitespace_in_name(self):
        name = factory.make_name('with space')
        content = factory.make_string().encode('ascii')
        uploaded_file = SimpleUploadedFile(content=content, name=name)
        form = CommissioningScriptForm(files={'content': uploaded_file})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            ["Name contains disallowed characters (e.g. space or quotes)."],
            form._errors['content'])

    def test_rejects_quotes_in_name(self):
        name = factory.make_name("l'horreur")
        content = factory.make_string().encode('ascii')
        uploaded_file = SimpleUploadedFile(content=content, name=name)
        form = CommissioningScriptForm(files={'content': uploaded_file})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            ["Name contains disallowed characters (e.g. space or quotes)."],
            form._errors['content'])


class TestUnconstrainedMultipleChoiceField(MAASServerTestCase):

    def test_accepts_list(self):
        value = ['a', 'b']
        instance = UnconstrainedMultipleChoiceField()
        self.assertEqual(value, instance.clean(value))


class TestValidatorMultipleChoiceField(MAASServerTestCase):

    def test_field_validates_valid_data(self):
        value = ['test@example.com', 'me@example.com']
        field = ValidatorMultipleChoiceField(validator=validate_email)
        self.assertEqual(value, field.clean(value))

    def test_field_uses_validator(self):
        value = ['test@example.com', 'invalid-email']
        field = ValidatorMultipleChoiceField(validator=validate_email)
        error = self.assertRaises(ValidationError, field.clean, value)
        self.assertEquals(['Enter a valid email address.'], error.messages)
