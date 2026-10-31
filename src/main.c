#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "FreeRTOS.h"
#include "task.h"

#include "application/app.h"

#define APP_TASK_PRIORITY   ( tskIDLE_PRIORITY + 2 )
#define APP_TASK_STACK      ( configMINIMAL_STACK_SIZE * 4 )

static void prvShutdownHandler( int sig )
{
    ( void ) sig;
    _exit( 130 );
}

int main( void )
{
    /* The FreeRTOS POSIX port commandeers SIGUSR1 and SIGALRM. We only
     * install handlers for SIGINT/SIGTERM so Ctrl+C and `docker stop`
     * tear the process down cleanly. */
    signal( SIGINT,  prvShutdownHandler );
    signal( SIGTERM, prvShutdownHandler );
    setvbuf( stdout, NULL, _IOLBF, 0 );

    if( xTaskCreate( app_task, "app", APP_TASK_STACK, NULL, APP_TASK_PRIORITY, NULL ) != pdPASS )
    {
        printf( "failed to create app task\n" );
        return EXIT_FAILURE;
    }

    vTaskStartScheduler();

    /* Should never reach here. */
    for( ;; ) { }
    return EXIT_SUCCESS;
}

void vApplicationMallocFailedHook( void )
{
    printf( "ERROR: malloc failed\n" );
    abort();
}

void vAssertCalled( const char * const pcFileName, unsigned long ulLine )
{
    fprintf( stderr, "ASSERT: %s:%lu\n", pcFileName, ulLine );
    fflush( stderr );
    abort();
}

void vApplicationGetIdleTaskMemory( StaticTask_t ** ppxIdleTaskTCBBuffer,
                                    StackType_t ** ppxIdleTaskStackBuffer,
                                    configSTACK_DEPTH_TYPE * puxIdleTaskStackSize )
{
    static StaticTask_t xIdleTaskTCB;
    static StackType_t  uxIdleTaskStack[ configMINIMAL_STACK_SIZE ];
    *ppxIdleTaskTCBBuffer    = &xIdleTaskTCB;
    *ppxIdleTaskStackBuffer  = uxIdleTaskStack;
    *puxIdleTaskStackSize    = configMINIMAL_STACK_SIZE;
}

void vApplicationGetTimerTaskMemory( StaticTask_t ** ppxTimerTaskTCBBuffer,
                                     StackType_t ** ppxTimerTaskStackBuffer,
                                     configSTACK_DEPTH_TYPE * puxTimerTaskStackSize )
{
    static StaticTask_t xTimerTaskTCB;
    static StackType_t  uxTimerTaskStack[ configTIMER_TASK_STACK_DEPTH ];
    *ppxTimerTaskTCBBuffer   = &xTimerTaskTCB;
    *ppxTimerTaskStackBuffer = uxTimerTaskStack;
    *puxTimerTaskStackSize   = configTIMER_TASK_STACK_DEPTH;
}
