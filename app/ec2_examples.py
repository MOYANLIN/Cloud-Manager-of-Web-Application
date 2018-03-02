from flask import Flask, render_template, g, redirect, url_for, request, session, flash
from app import webapp
from .mysqlconnection import MySQLConnector

import boto3
from app import config
from datetime import datetime, timedelta
from operator import itemgetter
import threading
import time


webapp.config['AWS_ACCESS_KEY_ID']='AKIAJPGHEVXS42EKMEXQ'
webapp.config['AWS_SECRET_ACCESS_KEY']='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'
webapp.config['BUCKET_NAME']='whileyouweresleeping'
webapp.secret_key='ece1779'

open_auto_scaling = False

scripts= """#!/bin/bash
cd home/ubuntu/Desktop/Myproject
source venv/bin/activate
./run2.sh &
"""

# scripts=open('scripts.txt','r').read()
# file.seek(0)
@webapp.route('/all_workers',methods=['GET'])
# Display an HTML list of all ec2 instances
def ec2_list():
    client = boto3.client('elb',
aws_access_key_id=webapp.config['AWS_ACCESS_KEY_ID'],
aws_secret_access_key=webapp.config['AWS_SECRET_ACCESS_KEY'])
    ec2 = boto3.resource('ec2')
    response = client.describe_load_balancers(
        LoadBalancerNames=[
            'MyLoadBalancer',
        ],
    )
    loadB_instances = response['LoadBalancerDescriptions'][0]['Instances']
    instanceId = []
    for loadB_instance in loadB_instances:
        instanceId.append(loadB_instance['InstanceId'])
    instances = ec2.instances.filter(InstanceIds=instanceId)
    

    return render_template("managerUI/list.html",title="All EC2 Instances",instances=instances)


@webapp.route('/all_workers/<id>',methods=['GET'])
#Display details about a specific instance.
def ec2_view(id):
    ec2 = boto3.resource('ec2')

    instance = ec2.Instance(id)

    client = boto3.client('cloudwatch')

    metric_name = 'CPUUtilization'

    ##    CPUUtilization, NetworkIn, NetworkOut, NetworkPacketsIn,
    #    NetworkPacketsOut, DiskWriteBytes, DiskReadBytes, DiskWriteOps,
    #    DiskReadOps, CPUCreditBalance, CPUCreditUsage, StatusCheckFailed,
    #    StatusCheckFailed_Instance, StatusCheckFailed_System


    namespace = 'AWS/EC2'
    statistic = 'Average'                   # could be Sum,Maximum,Minimum,SampleCount,Average



    cpu = client.get_metric_statistics(
        Period=1 * 60,
        StartTime=datetime.utcnow() - timedelta(seconds=60 * 60),
        EndTime=datetime.utcnow() - timedelta(seconds=0 * 60),
        MetricName=metric_name,
        Namespace=namespace,  # Unit='Percent',
        Statistics=[statistic],
        Dimensions=[{'Name': 'InstanceId', 'Value': id}]
    )

    cpu_stats = []


    for point in cpu['Datapoints']:
        hour = point['Timestamp'].hour
        minute = point['Timestamp'].minute
        time = hour + minute/60
        cpu_stats.append([time,point['Average']])

    cpu_stats = sorted(cpu_stats, key=itemgetter(0))



    return render_template("managerUI/view.html",title="Instance Info",
                           instance=instance,
                           cpu_stats=cpu_stats,

                           )


@webapp.route('/all_workers/grow',methods=['POST'])
# Start a new EC2 instance
def ec2_create():

    ec2 = boto3.resource('ec2')

    crt_instance = ec2.create_instances(ImageId=config.ami_id,
                         InstanceType="t2.micro",
                         MinCount=1,
                         MaxCount=1,
                         KeyName='ece1779a2',
                         UserData=scripts,
                         #UserData= base64.b64decode(bytes(scripts, 'utf-8')),
                         SecurityGroupIds=['sg-1566d767'],
                         Monitoring={
                             'Enabled': True
                         },
                         )
    client = boto3.client('elb')
    client.register_instances_with_load_balancer(
        Instances=[
            {
                'InstanceId': crt_instance[0].id,
            },
        ],
        LoadBalancerName='MyLoadBalancer',
    )
    return redirect(url_for('ec2_list'))

@webapp.route('/all_workers/shrink/<id>',methods=['POST'])
#Shrink a EC2 instance
def ec2_shrink(id):

    client = boto3.client('elb')
    ec2_client = boto3.client('ec2')
    client.deregister_instances_from_load_balancer(
         LoadBalancerName='MyLoadBalancer',
         Instances=[
             {
                 'InstanceId': id,
             },
         ],
     )
    ec2_client.terminate_instances(
         DryRun = False,
         InstanceIds = [id]
     )
    return redirect(url_for('ec2_list'))

@webapp.route('/open_auto_scaling',methods=['POST','GET'])
def open_auto_scaling():

    if  request.form.get('up_threshold').isdigit() == False or \
    request.form.get('down_threshold').isdigit() == False or \
    request.form.get('expand_ratio').isdigit() == False or \
    request.form.get('shrink_ratio').isdigit() == False:
        return "Error! You should type interger!"

    elif  request.form.get('up_threshold').isdigit() == True and \
        request.form.get('down_threshold').isdigit() == True and \
        request.form.get('expand_ratio').isdigit() == True and \
        request.form.get('shrink_ratio').isdigit() == True:
        up_threshold = int(request.form.get('up_threshold'))
        down_threshold = int(request.form.get('down_threshold'))
        expand_ratio = int(request.form.get('expand_ratio'))
        shrink_ratio = int(request.form.get('shrink_ratio'))

    global open_auto_scaling
    open_auto_scaling = True

    #open background thread
    bg_thread = threading.Thread(target=auto_check, args=(1, lambda: open_auto_scaling, 5*60 ,up_threshold,down_threshold,expand_ratio,shrink_ratio))
    bg_thread.start()

    return redirect(url_for('ec2_list'))

#method for checking CPU status every given interval
def auto_check(id,stop,interval,up_threshold,down_threshold,expand_ratio,shrink_ratio):
    isauto = True
    while isauto==True:
        cpu_ave = get_cpu_average()
       # cpu_ave = int(10)
        print (cpu_ave)
        #overload
        if stop() != True:
            isauto==False
            break
        if cpu_ave > up_threshold:
            expand_instances(expand_ratio)
        #lack of load
        if cpu_ave < down_threshold:
            shrink_instance(shrink_ratio)

        time.sleep(interval)

def get_cpu_average():
    client = boto3.client('elb')

    metric_name = 'CPUUtilization'
    namespace = 'AWS/EC2'
    statistic = 'Average'
    response = client.describe_load_balancers(
        LoadBalancerNames=[
            'MyLoadBalancer',
        ],
    )
    instances = response['LoadBalancerDescriptions'][0]['Instances']
    cpu_array = []  # stores all instances' cpu at the latest momment
    for instance in instances:
        client = boto3.client('cloudwatch')
        instanceId = instance['InstanceId']

        cpu = client.get_metric_statistics(
            Period=1 * 60,
            StartTime=datetime.utcnow() - timedelta(seconds=60 * 60),
            EndTime=datetime.utcnow() - timedelta(seconds=0 * 60),
            MetricName=metric_name,
            Namespace=namespace,
            Statistics=[statistic],
            Dimensions=[{'Name': 'InstanceId', 'Value': instanceId}]
        )
        cpu_stats = []

        for point in cpu['Datapoints']:
            hour = point['Timestamp'].hour
            minute = point['Timestamp'].minute
            time = hour + minute/60
            cpu_stats.append([time, point['Average']])

        cpu_stats = sorted(cpu_stats, key=itemgetter(0))
        if len(cpu_stats) != 0:
            cpu_array.append(cpu_stats[len(cpu_stats) - 1][1])

    cpu_avg = float(sum(cpu_array)) / len(cpu_array)
    return cpu_avg

def expand_instances(ratio):
    cur_num = get_cur_num_instances()
    add_num= (ratio - 1) * cur_num
    #a ratio of 2 doubles the number of workers


    ec2 = boto3.resource('ec2')

    crt_instance = ec2.create_instances(ImageId=config.ami_id,
                         InstanceType="t2.micro",
                         MinCount=add_num,
                         MaxCount=add_num,
                         KeyName='ece1779a2',
                         UserData=scripts,
                         #UserData= base64.b64decode(bytes(scripts, 'utf-8')),
                         SecurityGroupIds=['sg-1566d767'],
                         Monitoring={
                             'Enabled': True
                         },
                         )
    client = boto3.client('elb')

    for i in range(0, add_num):
        client.register_instances_with_load_balancer(
            Instances=[
                {
                    'InstanceId': crt_instance[i].id,
                },
            ],
            LoadBalancerName='MyLoadBalancer',
        )

def shrink_instance(ratio):
    cur_num = get_cur_num_instances()
    shrink_num = int((ratio - 1)/ratio * cur_num)
    #a ratio of 4 shrink 75% of the workers
    if cur_num == 1:
        shrink_num = 0

    client = boto3.client('elb')
    ec2_client = boto3.client('ec2')
    response = client.describe_load_balancers(
        LoadBalancerNames=[
            'MyLoadBalancer',
        ],
    )
    instances = response['LoadBalancerDescriptions'][0]['Instances']

    for i in range(0, shrink_num):

        client.deregister_instances_from_load_balancer(
            LoadBalancerName='MyLoadBalancer',
            Instances=[
                {
                    'InstanceId': instances[i]['InstanceId']
                },
            ]
        )
        ec2_client.terminate_instances(
            DryRun=False,
            InstanceIds=[instances[i]['InstanceId'], ]
        )


def get_cur_num_instances():
    client = boto3.client('elb')
    response = client.describe_load_balancers(
        LoadBalancerNames=[
            'MyLoadBalancer',
        ],
    )
    instances = response['LoadBalancerDescriptions'][0]['Instances']
    return len(instances)

@webapp.route('/close_auto_scaling',methods=['POST'])
def close_auto_scaling():
    #close background thread
    global open_auto_scaling
    open_auto_scaling = False

    return redirect(url_for('ec2_list'))


@webapp.route('/all_workers/delete_all', methods=['post'])
def delete_all():
    mysql = MySQLConnector(webapp, 'ece1779')
    #delete all the data in database
    query="truncate image"
    mysql.query_db(query)
    query="truncate user"
    mysql.query_db(query)

    #delete all the images in s3
    s3=boto3.resource('s3')
    bucket=s3.Bucket(webapp.config['BUCKET_NAME'])
    for each in bucket.objects.all():
        each.delete()
    return redirect(url_for('ec2_list'))
