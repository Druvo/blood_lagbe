$(document).ready(function() {
    "use strict";

    PageScroll();


    // Loading Box (Preloader)
    function handlePreloader() {
        if ($('.preloader').length > 0) {
            $('.preloader').delay(200).fadeOut(500);
        }
    }

    function PageLoad() {
      $( window ).on( "load", function() {
            setInterval(function(){ 
                $('.preloader-wrap').fadeOut(300);
            }, 400);
            setInterval(function(){ 
                $('body').addClass('loaded');
            }, 600); 
      });
    }


    handlePreloader();
    PageLoad();



    $(window).on('load',function(){
        $('#Modalstories').modal('show');
    });
    

    $('.emoji-bttn').on('click', function() {
      $(this).parent().find('.emoji-wrap').toggleClass('active');
      return false;
    });

    $('.add-wishlist').on('click', function() {
        $(this).toggleClass('bg-greylight bg-danger');
        $(this).find('i').toggleClass('text-grey-900 text-white')
        return false;
    });

    $('.question-div .question-ans').on('click', function() {
        $('.question-ans').removeClass('active');
        $(this).addClass('active');
        return false;
    });

    $('.question-div .question-ans').on('click', function() {
        $('.question-ans').removeClass('active');
        $(this).addClass('active');
        return false;
    });

    $('.next-bttn').on('click', function() {
        var next_bttn_id =  $(this).attr('data-question');
        $('.question-div .card-body').fadeOut(0);
        $('.question-div #'+next_bttn_id ).fadeIn(500);
        // alert(next_bttn_id);
        // $(this).addClass('active');
        return false;
    });

    
    
    $('.dropdown-menu-icon').on('click', function() {
        $('.dropdown-menu-settings').toggleClass('active');
    });
    


    
    $('.menu-search-icon').on('click', function() {
        $('.app-header-search').addClass('show');
    });
    $('.searchbox-close').on('click', function() {
        $('.app-header-search').removeClass('show');
    });
    
    

    $('.switchcolor').on('click', function() {
        $(this).addClass('active');
        $('.backdrop').addClass('active');
        $('.switchcolor-wrap').addClass('active'); 
    });

    $('.sheet-close,.backdrop').on('click', function() {
        $('.switchcolor').removeClass('active');
        $('.backdrop').removeClass('active');
        $('.switchcolor-wrap').removeClass('active'); 
    });

    $('#darkmodeswitch').on('change', function () {
        if (this.checked) {
            $('body').addClass('theme-dark');
        } else {
            $('body').removeClass('theme-dark');
        }
    });


    

    $('.chat-active-btn').on('click', function() {
        $('.right-chat').toggleClass('active-sidebar');
        $('.main-content').toggleClass('right-chat-active');
        return false;
    });

    $(window).resize(function(){
        if($(this).width() < 1600){
            $('.right-chat').removeClass('active-sidebar');
        }
        else{
            $('.right-chat').addClass('active-sidebar');
        }
    })

    if($(window).width() < 1600){
        $('.right-chat').removeClass('active-sidebar');
    }
    else{
        $('.right-chat').addClass('active-sidebar');
    }

    $(window).scroll(function(){
        if ($(this).scrollTop() > 10) {
           $('.scroll-header').addClass('bg-white shadow-xss');
        } else {
           $('.scroll-header').removeClass('bg-white shadow-xss');
        }
    });

    $(window).scroll(function(){
        if ($(this).scrollTop() > 10) {
           $('.middle-sidebar-header').addClass('scroll');
        } else {
           $('.middle-sidebar-header').removeClass('scroll');
        }
    });

    
    $('.nav-menu').on('click', function () {
        $(this).toggleClass('active');
        $('.navigation').toggleClass('nav-active');
    });

    $('.model-popup-chat').on('click', function () {
        $('.modal-popup-chat').toggleClass('d-block');
        return false;
    });
    $('.modal-popup-chat a').on('click', function () {
        $('.modal-popup-chat').removeClass('d-block');
        return false;
    });

    

    

    $('.close-nav').on('click', function () {
        $('.navigation').removeClass('nav-active');
        return false;
    });

    
    $('.toggle-menu-color input').on('change', function () {
        if (this.checked) {
            $('.navigation').addClass('menu-current-color');
        } else {
            $('.navigation').removeClass('menu-current-color');
        }
    });

    $('.toggle-menu input').on('change', function () {
        if (this.checked) {
            $('.navigation,.main-content').addClass('menu-active');
        } else {
            $('.navigation,.main-content').removeClass('menu-active');
        }
    });
    
    $('.toggle-screen input').on('change', function () {
        if (this.checked) {
            $('body').addClass('theme-full');
        } else {
            $('body').removeClass('theme-full');
        }
    });
    
    
    $('.toggle-dark input').on('change', function () {
        if (this.checked) {
            $('body').addClass('theme-dark');
        } else {
            $('body').removeClass('theme-dark');
        }
    });

    $('input[name="color-radio"]').on('change', function () {
        if (this.checked) {
          $('body').removeClass('color-theme-teal color-theme-cadetblue color-theme-pink color-theme-deepblue color-theme-blue color-theme-red color-theme-black color-theme-gray color-theme-orange color-theme-yellow color-theme-green color-theme-white color-theme-brown color-theme-darkgreen color-theme-deeppink color-theme-darkorchid');
          $('body').addClass('color-theme-' + $(this).val());
        }
    });

    

});





function PageScroll() {
   $(".scroll-tiger").on("click", function(e) {
        var $anchor = $(this);
        $("html, body").stop().animate({
            scrollTop: $($anchor.attr("href")).offset().top - 0
        }, 1500, 'easeInOutExpo');
        $('.overlay-section').removeClass('active'); 
        e.preventDefault();

    });
}

$(document).ready(function() {
    $(document).scroll(function () {
        var scroll = $(this).scrollTop();
        var topDist = $(".main-content").position();
        if (scroll > topDist.top) {
            $('#navbar').css({"height":"75px","position":"fixed","top":"0", "right":"0","left":"0"}).addClass('shadow-xs');  
            $('#to-top').css({"display":"block"});
        } else {
            $('#navbar').css({"height":"100px","position":"static","top":"auto", "right":"auto","left":"auto"}).removeClass('shadow-xs');
            $('#to-top').css({"display":"none"});
        }
    });
});
