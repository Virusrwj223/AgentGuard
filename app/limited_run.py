"""Dedicated child process; terminate descendants when its Windows job closes."""
import os
import sys
if __name__ == "__main__":
    if os.name=="nt":
        import subprocess
        import win32api, win32job
        job=win32job.CreateJobObject(None,"")
        info=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"]=win32job.JOB_OBJECT_LIMIT_JOB_MEMORY | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info["JobMemoryLimit"]=768*1024**2
        win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,info)
        win32job.AssignProcessToJobObject(job,win32api.GetCurrentProcess())
        sys.exit(subprocess.call(sys.argv[1:]))
    else:
        import resource
        for key,limit in [(resource.RLIMIT_AS,768*1024**2),(resource.RLIMIT_CPU,7),(resource.RLIMIT_FSIZE,1024**2)]:
            resource.setrlimit(key,(limit,limit))
        os.execv(sys.argv[1],sys.argv[1:])
