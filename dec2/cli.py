from __future__ import print_function, division, absolute_import

import click

import dec2
from .cluster import Cluster
from .exceptions import DEC2Exception
from .config import setup_logging
from .utils import Table


def start():
    import sys
    import logging
    import traceback

    try:
        setup_logging(logging.DEBUG)
        cli(obj={})
    except DEC2Exception as e:
        click.echo("ERROR: %s" % e, err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo(
            "Interrupted by Ctrl-C. One or more actions could be still running in the cluster")
        sys.exit(1)
    except Exception as e:
        click.echo(traceback.format_exc(), err=True)
        sys.exit(1)


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(prog_name="dec2", version=dec2.__version__)
@click.pass_context
def cli(ctx):
    ctx.obj = {}


@cli.command(short_help="Launch instances")
@click.option("--name", required=True, help="Tag name on EC2")
@click.option("--keyname", required=True, help="Keyname on EC2 console")
@click.option("--keypair", required=True, type=click.Path(exists=True), help="Path to the keypair that matches the keyname")
@click.option("--ami", default="ami-d05e75b8", show_default=True, required=False, help="EC2 AMI")
@click.option("--username", default="ubuntu", show_default=True, required=False, help="User to SSH to the AMI")
@click.option("--type", "instance_type", default="m3.2xlarge", show_default=True, required=False, help="EC2 Instance Type")
@click.option("--count", default=4, show_default=True, required=False, help="Number of nodes")
@click.option("--security-group", default="dec2-default", show_default=True, required=False, help="Security Group Name")
@click.option("--volume-type", default="gp2", show_default=True, required=False, help="Root volume type")
@click.option("--volume-size", default=500, show_default=True, required=False, help="Root volume size (GB)")
@click.option("--file", "filepath", type=click.Path(), default="cluster.yaml", show_default=True, required=False, help="File to save the metadata")
@click.option("--ssh-check/--no-ssh-check", default=True, show_default=True, required=False, help="Whether to check or not for SSH connection")
@click.option("--provision/--no-provision", "_provision", default=True, show_default=True, required=False, help="Provision salt on the nodes")
@click.pass_context
def up(ctx, name, keyname, keypair, ami, username, instance_type, count, security_group, volume_type, volume_size, filepath, ssh_check, _provision):
    import yaml
    from .ec2 import EC2

    click.echo("Launching nodes")
    driver = EC2(image=ami, instance_type=instance_type, count=count, keyname=keyname,
                 security_groups=[security_group], volume_type=volume_type, volume_size=volume_size,
                 name=name)
    instances = driver.launch()
    cluster = Cluster.from_boto3_instances(instances)
    cluster.set_username(username)
    cluster.set_keypair(keypair)
    with open(filepath, "w") as f:
        yaml.safe_dump(cluster.to_dict(), f, default_flow_style=False)

    if ssh_check:
        click.echo("Checking SSH connection to nodes")
        cluster = Cluster.from_filepath(filepath)
        info = cluster.check_ssh()
        data = [["Node IP", "SSH check"]]
        for ip, status in info.items():
            data.append([ip, status])
        t = Table(data, 1)
        t.write()

    if _provision:
        ctx.invoke(provision, filepath=filepath)


@cli.command(short_help="SSH to one of the node. 0-index")
@click.argument('node', required=False, default=0)
@click.option("--file", "filepath", type=click.Path(exists=True), default="cluster.yaml", show_default=True, required=False, help="Filepath to the instances metadata")
def ssh(node, filepath):
    import os
    import subprocess
    cluster = Cluster.from_filepath(filepath)
    instance = cluster.instances[node]
    ip = instance.ip
    username = instance.username
    keypair = os.path.expanduser(instance.keypair)
    cmd = ['ssh', username + '@' + ip]
    cmd = cmd + ['-i', keypair]
    cmd = cmd + ['-oStrictHostKeyChecking=no']
    cmd = cmd + ['-p %i' % instance.port]
    click.echo(' '.join(cmd))
    subprocess.call(cmd)


@cli.command(short_help="Provision salt instances")
@click.option("--file", "filepath", type=click.Path(exists=True), default="cluster.yaml", show_default=True, required=False, help="Filepath to the instances metadata")
@click.option("--master/--no-master", is_flag=True, default=True, show_default=True, help="Bootstrap the salt master")
@click.option("--minions/--no-minions", is_flag=True, default=True, show_default=True, help="Bootstrap the salt minions")
@click.option("--upload/--no-upload", is_flag=True, default=True, show_default=True, help="Upload the salt formulas")
def provision(filepath, master, minions, upload):
    from .salt import install_salt_master, install_salt_minion, upload_formulas
    cluster = Cluster.from_filepath(filepath)
    if master:
        click.echo("Bootstraping salt master")
        install_salt_master(cluster)
    if minions:
        click.echo("Bootstraping salt minions")
        install_salt_minion(cluster)
    if upload:
        click.echo("Uploading salt formulas")
        upload_formulas(cluster)


@cli.command("dask-distributed", short_help="Start a dask.distributed cluster")
@click.option("--file", "filepath", type=click.Path(exists=True), default="cluster.yaml", show_default=True, required=False, help="Filepath to the instances metadata")
def dask_distributed(filepath):
    cluster = Cluster.from_filepath(filepath)
    click.echo("Installing scheduler")
    cluster.pepper.local("node-0", "grains.append", ["roles", "dask.distributed.scheduler"])
    output = cluster.pepper.local("node-0", "state.sls", ["dask.distributed.scheduler"])
    print_state(output)

    click.echo("Installing workers")
    cluster.pepper.local("node-[1-9]*", "grains.append", ["roles", "dask.distributed.worker"])
    output = cluster.pepper.local("node-[1-9]*", "state.sls", ["dask.distributed.worker"])
    print_state(output)


@cli.command("cloudera-manager", short_help="Start a Cloudera manager cluster")
@click.option("--file", "filepath", type=click.Path(exists=True), default="cluster.yaml", show_default=True, required=False, help="Filepath to the instances metadata")
def cloudera_manager(filepath):
    cluster = Cluster.from_filepath(filepath)
    click.echo("Installing Cloudera Manager")
    cluster.pepper.local("node-0", "grains.append", ["roles", "cloudera.manager.server"])
    cluster.pepper.local("node-*", "grains.append", ["roles", "cloudera.manager.agent"])
    output = cluster.pepper.local("node-*", "state.sls", ["cloudera.manager.cluster"])
    print_state(output)


def print_state(output):
    from .salt import Response
    response = Response.from_dict(output)
    response = response.aggregate_by(field="result")
    data = [["Node ID", "# Successful", "# Failed"]]
    data.extend(response.aggregated_to_table(agg=len))
    t = Table(data, 1)
    t.write()

    for node_id, data in response.items():
        failed = data["failed"]
        if len(failed):
            click.echo("Failed states for '{}'".format(node_id))
            for fail in failed:
                name = fail["name"].replace("_|-", " | ")
                click.echo("  {name}: {comment}".format(name=name, comment=fail["comment"]))